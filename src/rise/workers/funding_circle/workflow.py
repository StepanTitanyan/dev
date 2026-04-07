import json
import time
import logging
import requests
from rise.workers.funding_circle.matching.company import find_best_company_match
from rise.workers.funding_circle.matching.owner import (
    extract_majority_executive_business_owners,
    resolve_executive_business_owners_from_next_action,
    validate_resolved_executive_business_owners)
from rise.workers.funding_circle.payloads import (
    build_company_search_params,
    build_eligibility_payload,
    build_applicant_detail_payload,
    build_loan_application_details_payload,
    build_company_performance_payload,
    build_contact_details_payload,
    build_executive_business_owners_payload)
from rise.workers.funding_circle.client import (
    company_search,
    eligibility_check,
    get_applicant_details,
    get_loan_application_details,
    get_company_performance_details,
    get_contact_details,
    select_executive_business_owners,
    perform_next_step,
    get_presigned_upload_url,
    upload_document_to_s3,
    create_document,
    amend_bank_statements)
from rise.db.repositories import (
    get_application_by_id,
    update_application_current_step,
    update_working_payload,
    set_external_id,
    create_step,
    complete_step,
    fail_step)
from rise.workers.funding_circle.files import (
    rename_processing_folder,
    list_documents_by_type,
    get_or_create_application_folder,
    save_downloaded_documents)
from rise.salesforce.client import download_content_version
from rise.logging_context import update_fc_application_id, set_db_context, get_db_context
from rise.db.repositories import (
    log_application_event,
    EVT_STEP_STARTED, EVT_STEP_COMPLETED, EVT_STEP_FAILED,
    EVT_SF_DOWNLOAD_STARTED, EVT_SF_DOWNLOAD_DONE, EVT_SF_DOWNLOAD_FAILED)

logger = logging.getLogger(__name__)

STEP_ORDER = {
    "eligibility_check": 1,
    "get_applicant_details": 2,
    "get_loan_application_details": 3,
    "get_company_performance_details": 4,
    "get_contact_details": 5,
    "select_executive_business_owners": 6,
    "submit_bank_statements": 7,
    "identify_executive_business_owners": 8,
    "application_submitted": 9}

############################################ HELPER ############################################
def poll_until_state(session: requests.Session, application_id: str, target_state: str | set[str] | list[str] = "get_applicant_details", interval_seconds: int = 3, max_attempts: int = 20):
    terminal_error_states = {"company_has_in_flight_app_error", "user_has_in_flight_loan_application", "error", "invalid_application", "rejected", "reject_application"}
    pollable_states = {"awaiting_next_action"}
    final_success_states = {"application_submitted", "submitted", "completed", "identify_executive_business_owners"}

    if isinstance(target_state, str):
        target_states = {target_state}
    else:
        target_states = set(target_state)

    history = []

    for attempt in range(1, max_attempts + 1):
        logger.debug("[WORKFLOW] Polling next_action application_id=%s attempt=%s/%s", application_id, attempt, max_attempts)
        data = perform_next_step(session=session, application_id=application_id)
        history.append(data)

        state = data.get("type")
        logger.debug("[WORKFLOW] next_action state=%s application_id=%s attempt=%s", state, application_id, attempt)

        if state in target_states:
            logger.info("[WORKFLOW] Reached target state=%s application_id=%s attempts=%s", state, application_id, attempt)
            return {
                "success": True,
                "final_state": state,
                "response": data,
                "history": history}

        if "application_submitted" in target_states and state in final_success_states:
            logger.info("[WORKFLOW] Reached final success state=%s application_id=%s attempts=%s", state, application_id, attempt)
            return {
                "success": True,
                "final_state": state,
                "response": data,
                "history": history}

        if state in terminal_error_states:
            logger.warning("[WORKFLOW] Terminal error state reached: %s application_id=%s", state, application_id)
            return {
                "success": False,
                "final_state": state,
                "response": data,
                "history": history,
                "error": "Terminal state reached: %s" % state}

        if state in pollable_states:
            logger.debug("[WORKFLOW] Still waiting state=%s application_id=%s sleeping=%ss", state, application_id, interval_seconds)
            time.sleep(interval_seconds)
            continue

        logger.warning("[WORKFLOW] Unexpected state=%s application_id=%s", state, application_id)
        return {
            "success": False,
            "final_state": state,
            "response": data,
            "history": history,
            "error": "Unexpected state returned by next_action: %s" % state}

    logger.warning("[WORKFLOW] Polling timed out after %s attempts application_id=%s", max_attempts, application_id)
    return {
        "success": False,
        "final_state": "timeout",
        "response": None,
        "history": history,
        "error": "Did not reach target state '%s' after %s attempts." % (target_state, max_attempts)}


def _rename_processing_folder_if_needed(salesforce_payload: dict, application_id: str):
    system = salesforce_payload.get("system") or {}
    processing_tracking_id = system.get("processing_tracking_id")
    processing_files_dir = system.get("processing_files_dir")

    if not processing_tracking_id or not application_id:
        return salesforce_payload

    if processing_files_dir and processing_files_dir.endswith("/%s" % application_id):
        return salesforce_payload

    new_folder_path = rename_processing_folder(processing_tracking_id, application_id)

    system["processing_tracking_id"] = application_id
    system["processing_files_dir"] = new_folder_path

    for document in system.get("uploaded_documents") or []:
        if document.get("local_path"):
            filename = document.get("filename")
            document["local_path"] = "%s/%s" % (new_folder_path, filename)

    salesforce_payload["system"] = system
    return salesforce_payload


def persist_workflow_progress(db, db_application_id: int, current_step: str, result: dict):
    if not db or not db_application_id:
        return result

    application = get_application_by_id(db, db_application_id)
    if not application:
        return result

    returned_payload = result.get("salesforce_payload")
    if returned_payload:
        update_working_payload(db, application, returned_payload)
        application = get_application_by_id(db, db_application_id)

    returned_fc_application_id = result.get("application_id")
    if current_step == "eligibility_check" and returned_fc_application_id:
        if application.external_id != returned_fc_application_id:
            set_external_id(db, application, returned_fc_application_id)

        # Update the log context so all subsequent log lines carry the FC application ID
        update_fc_application_id(returned_fc_application_id)

        updated_payload = result.get("salesforce_payload") or application.working_payload_json or application.raw_input_json
        updated_payload = _rename_processing_folder_if_needed(updated_payload, returned_fc_application_id)
        result["salesforce_payload"] = updated_payload
        update_working_payload(db, application, updated_payload)
        application = get_application_by_id(db, db_application_id)

    if result.get("success"):
        update_application_current_step(db, application, result.get("step"), None)
    else:
        update_application_current_step(db, application, current_step, result.get("message"))

    return result


def run_persisted_step(db, db_application_id: int, current_step: str, step_callable, session: requests.Session, salesforce_payload: dict, application_id: str | None = None):
    application = get_application_by_id(db, db_application_id)
    if application:
        update_application_current_step(db, application, current_step, None)

    step_request_json = {
        "salesforce_payload": salesforce_payload,
        "application_id": application_id}

    step_row = create_step(
        db=db,
        application_id=db_application_id,
        step_name=current_step,
        step_order=STEP_ORDER[current_step],
        status="started",
        request_json=step_request_json)

    log_application_event(
        db, db_application_id, EVT_STEP_STARTED,
        "Step %s started" % current_step,
        {"step": current_step, "fc_application_id": application_id})

    # Store db context so step functions that don't receive db as a parameter
    # (e.g. step_submit_bank_statements) can still write application events.
    set_db_context(db, db_application_id)

    step_start = time.time()
    try:
        if application_id is None:
            result = step_callable(session, salesforce_payload)
        else:
            result = step_callable(session, salesforce_payload, application_id)
    except Exception as exc:
        elapsed = round(time.time() - step_start, 2)
        error_str = "%s: %s" % (type(exc).__name__, exc)
        logger.error(
            "[WORKFLOW] Step %s raised exception after %ss db_application_id=%s error=%s: %s",
            current_step, elapsed, db_application_id, type(exc).__name__, exc)
        fail_step(db, step_row, error_str)
        log_application_event(
            db, db_application_id, EVT_STEP_FAILED,
            "Step %s crashed — %s" % (current_step, error_str),
            {"step": current_step, "elapsed_s": elapsed, "error": error_str})
        application = get_application_by_id(db, db_application_id)
        if application:
            update_application_current_step(db, application, current_step, error_str)
        raise

    elapsed = round(time.time() - step_start, 2)
    result = persist_workflow_progress(db, db_application_id, current_step, result)

    if result.get("success"):
        complete_step(db, step_row, response_json=result)
        logger.info("[WORKFLOW] Step %s completed in %ss db_application_id=%s", current_step, elapsed, db_application_id)
        log_application_event(
            db, db_application_id, EVT_STEP_COMPLETED,
            "Step %s completed in %ss" % (current_step, elapsed),
            {"step": current_step, "elapsed_s": elapsed,
             "next_step": result.get("step"),
             "fc_application_id": result.get("application_id")})
    else:
        fail_step(db, step_row, result.get("message") or "Unknown step failure", response_json=result)
        logger.warning(
            "[WORKFLOW] Step %s failed after %ss db_application_id=%s reason=%s",
            current_step, elapsed, db_application_id, result.get("message"))
        log_application_event(
            db, db_application_id, EVT_STEP_FAILED,
            "Step %s failed — %s" % (current_step, result.get("message")),
            {"step": current_step, "elapsed_s": elapsed,
             "error": result.get("message"),
             "retryable": result.get("retryable"),
             "stage": result.get("stage")})

    return result
################################################################################################

def run_application_workflow(salesforce_payload: dict | None = None, step: str = "eligibility_check", session: requests.Session = None, application_id: str = None, db = None, db_application_id: int | None = None):
    if salesforce_payload is None:
        return {
            "success": False,
            "step": None,
            "stage": None,
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": "No data received",
            "retryable": False}

    normal_states = {
        "eligibility_check",
        "get_applicant_details",
        "get_loan_application_details",
        "get_company_performance_details",
        "get_contact_details",
        "select_executive_business_owners",
        "submit_bank_statements",
        "identify_executive_business_owners",
        "application_submitted"}

    if step not in normal_states and step is not None:
        return {
            "success": False,
            "step": step,
            "stage": step,
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": "Unknown stage",
            "retryable": False}

    if not session:
        return {
            "success": False,
            "step": step,
            "stage": step,
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": "Authenticated session is required",
            "retryable": True}

    response = None

    if db and db_application_id and step in STEP_ORDER:
        if step == "eligibility_check":
            response = run_persisted_step(db, db_application_id, step, step_eligibility_check, session, salesforce_payload)
        elif step == "get_applicant_details":
            response = run_persisted_step(db, db_application_id, step, step_get_applicant_details, session, salesforce_payload, application_id)
        elif step == "get_loan_application_details":
            response = run_persisted_step(db, db_application_id, step, step_get_loan_application_details, session, salesforce_payload, application_id)
        elif step == "get_company_performance_details":
            response = run_persisted_step(db, db_application_id, step, step_get_company_performance_details, session, salesforce_payload, application_id)
        elif step == "get_contact_details":
            response = run_persisted_step(db, db_application_id, step, step_get_contact_details, session, salesforce_payload, application_id)
        elif step == "select_executive_business_owners":
            response = run_persisted_step(db, db_application_id, step, step_select_executive_business_owners, session, salesforce_payload, application_id)
        elif step == "submit_bank_statements":
            response = run_persisted_step(db, db_application_id, step, step_submit_bank_statements, session, salesforce_payload, application_id)
    else:
        if step == "eligibility_check":
            response = step_eligibility_check(session, salesforce_payload)
        elif step == "get_applicant_details":
            response = step_get_applicant_details(session, salesforce_payload, application_id)
        elif step == "get_loan_application_details":
            response = step_get_loan_application_details(session, salesforce_payload, application_id)
        elif step == "get_company_performance_details":
            response = step_get_company_performance_details(session, salesforce_payload, application_id)
        elif step == "get_contact_details":
            response = step_get_contact_details(session, salesforce_payload, application_id)
        elif step == "select_executive_business_owners":
            response = step_select_executive_business_owners(session, salesforce_payload, application_id)
        elif step == "submit_bank_statements":
            response = step_submit_bank_statements(session, salesforce_payload, application_id)

    if response is not None:
        if response.get("success") is False:
            return response
        step = response.get("step")
        application_id = response.get("application_id")
        salesforce_payload = response.get("salesforce_payload", salesforce_payload)

    if step == "identify_executive_business_owners":
        if db and db_application_id:
            application = get_application_by_id(db, db_application_id)
            if application:
                update_application_current_step(db, application, "identify_executive_business_owners", None)
        return {
            "success": True,
            "step": "identify_executive_business_owners",
            "stage": "identify_executive_business_owners",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "completion_status": "partially_successful",
            "message": "FundingCircle will contact via email within the next 24 hours to ask about the shareholder information.",
            "retryable": False}

    if step == "application_submitted":
        if db and db_application_id:
            application = get_application_by_id(db, db_application_id)
            if application:
                update_application_current_step(db, application, "application_submitted", None)
        return {
            "success": True,
            "step": "application_submitted",
            "stage": "application_submitted",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "completion_status": "fully_successful",
            "message": "Application was submitted successfully",
            "retryable": False}

    return run_application_workflow(salesforce_payload=salesforce_payload, step=step, session=session, application_id=application_id, db=db, db_application_id=db_application_id)


# ---------------------------------------------------------------------------------------------------
def step_eligibility_check(session: requests.Session, salesforce_payload: dict):
    company_section = salesforce_payload.get("company") or {}
    company_search_params = build_company_search_params(salesforce_payload)

    logger.info("[WORKFLOW] Searching company: search_string=%s type=%s",
                company_search_params["search_string"], company_search_params["type"])

    search_result = company_search(session=session, search_string=company_search_params["search_string"], company_type=company_search_params["type"])
    match_result = find_best_company_match(search_result=search_result, salesforce_company_name=company_section.get("company_name") or company_search_params["search_string"], salesforce_company_number=company_section.get("company_number"), min_score=75.0)

    if not match_result["success"]:
        logger.warning("[WORKFLOW] Company match failed: %s", match_result.get("reason"))
        return {
            "success": False,
            "step": "eligibility_check",
            "stage": "company_search",
            "application_id": None,
            "salesforce_payload": salesforce_payload,
            "message": "Could not confidently match company from search results. Match results: %s" % match_result,
            "retryable": False}

    selected_company = match_result["best_company"]
    logger.info("[WORKFLOW] Selected company: %s company_number=%s score=%s",
                selected_company.get("company_name"), selected_company.get("company_number"), match_result.get("score"))

    logger.info("[WORKFLOW] Submitting eligibility check...")
    eligibility_payload = build_eligibility_payload(company=selected_company, salesforce_payload=salesforce_payload)
    eligibility_result = eligibility_check(session=session, payload=eligibility_payload)
    application_id = eligibility_result.get("application_id")

    if not application_id:
        logger.error("[WORKFLOW] Eligibility check succeeded but application_id was missing")
        return {
            "success": False,
            "step": "eligibility_check",
            "stage": "eligibility_check",
            "application_id": None,
            "salesforce_payload": salesforce_payload,
            "message": "eligibility_checks succeeded but application_id was missing.",
            "eligibility_result": eligibility_result,
            "eligibility_payload": eligibility_payload,
            "retryable": True}

    salesforce_payload["system"] = salesforce_payload.get("system") or {}
    salesforce_payload["system"]["application_id"] = application_id
    logger.info("[WORKFLOW] Eligibility check passed application_id=%s", application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state="get_applicant_details",
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "eligibility_check",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    return {
        "success": True,
        "step": "get_applicant_details",
        "stage": "get_applicant_details",
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def step_get_applicant_details(session: requests.Session, salesforce_payload: dict, application_id: str):
    logger.info("[WORKFLOW] Submitting applicant details application_id=%s", application_id)
    applicant_detail_payload = build_applicant_detail_payload(salesforce_payload)
    get_applicant_details(session, applicant_detail_payload, application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state="get_loan_application_details",
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "get_applicant_details",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    return {
        "success": True,
        "step": "get_loan_application_details",
        "stage": "get_loan_application_details",
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def step_get_loan_application_details(session: requests.Session, salesforce_payload: dict, application_id: str):
    logger.info("[WORKFLOW] Submitting loan application details application_id=%s", application_id)
    loan_application_details_payload = build_loan_application_details_payload(salesforce_payload)
    get_loan_application_details(session, loan_application_details_payload, application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state="get_company_performance_details",
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "get_loan_application_details",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    return {
        "success": True,
        "step": "get_company_performance_details",
        "stage": "get_company_performance_details",
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def step_get_company_performance_details(session: requests.Session, salesforce_payload: dict, application_id: str):
    logger.info("[WORKFLOW] Submitting company performance details application_id=%s", application_id)
    company_performance_payload = build_company_performance_payload(salesforce_payload)
    get_company_performance_details(session, company_performance_payload, application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state="get_contact_details",
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "get_company_performance_details",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    return {
        "success": True,
        "step": "get_contact_details",
        "stage": "get_contact_details",
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def step_get_contact_details(session: requests.Session, salesforce_payload: dict, application_id: str):
    logger.info("[WORKFLOW] Submitting contact details application_id=%s", application_id)
    contact_details_payload = build_contact_details_payload(salesforce_payload)
    get_contact_details(session, contact_details_payload, application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state={"select_executive_business_owners", "get_bank_statements", "amend_bank_statements", "identify_executive_business_owners", "application_submitted"},
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "get_contact_details",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    resolved_state = next_action_result["response"].get("type")
    logger.info("[WORKFLOW] Contact details resolved next state=%s application_id=%s", resolved_state, application_id)

    if resolved_state in {"get_bank_statements", "amend_bank_statements", "parse_bank_statements"}:
        salesforce_payload["system"] = salesforce_payload.get("system") or {}
        salesforce_payload["system"]["bank_statements_action_type"] = resolved_state

        return {
            "success": True,
            "step": "submit_bank_statements",
            "stage": "submit_bank_statements",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "retryable": False}

    return {
        "success": True,
        "step": resolved_state,
        "stage": resolved_state,
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def step_select_executive_business_owners(session: requests.Session, salesforce_payload: dict, application_id: str):
    logger.info("[WORKFLOW] Resolving executive business owners application_id=%s", application_id)
    next_action_response = perform_next_step(session=session, application_id=application_id)

    potential_owners = (next_action_response.get("attributes") or {}).get("potential_executive_business_owners") or []
    logger.info("[WORKFLOW] FC returned %s potential executive business owners", len(potential_owners))

    resolve_result = resolve_executive_business_owners_from_next_action(
        salesforce_payload=salesforce_payload,
        next_action_response=next_action_response)

    if not resolve_result["success"]:
        logger.warning("[WORKFLOW] Owner resolution failed: %s", resolve_result.get("message"))
        return {
            "success": False,
            "step": "select_executive_business_owners",
            "stage": "owner_matching",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": resolve_result["message"],
            "retryable": False}

    salesforce_payload = resolve_result["salesforce_payload"]
    resolved_owners = resolve_result.get("resolved_executive_business_owners") or []
    logger.info("[WORKFLOW] Resolved %s executive business owners", len(resolved_owners))

    validation_result = validate_resolved_executive_business_owners(salesforce_payload)

    if not validation_result["success"]:
        logger.warning("[WORKFLOW] Owner validation failed: %s", validation_result.get("message"))
        return {
            "success": False,
            "step": "select_executive_business_owners",
            "stage": "owner_matching",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": validation_result["message"],
            "retryable": False}

    logger.info("[WORKFLOW] Submitting executive business owners application_id=%s", application_id)
    executive_business_owners_payload = build_executive_business_owners_payload(salesforce_payload)
    select_executive_business_owners(session, executive_business_owners_payload, application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state={"identify_executive_business_owners", "get_bank_statements", "amend_bank_statements", "application_submitted"},
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "select_executive_business_owners",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    resolved_state = next_action_result["response"].get("type")
    logger.info("[WORKFLOW] Executive business owners resolved next state=%s application_id=%s", resolved_state, application_id)

    if resolved_state in {"get_bank_statements", "amend_bank_statements", "parse_bank_statements"}:
        salesforce_payload["system"] = salesforce_payload.get("system") or {}
        salesforce_payload["system"]["bank_statements_action_type"] = resolved_state

        return {
            "success": True,
            "step": "submit_bank_statements",
            "stage": "submit_bank_statements",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "retryable": False}

    return {
        "success": True,
        "step": resolved_state,
        "stage": resolved_state,
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}


# ---------------------------------------------------------------------------------------------------
def _fetch_files_from_salesforce(content_version_ids: list[str], folder_path: str) -> dict:
    """
    Downloads each ContentVersion from Salesforce and saves the bytes to folder_path.
    Returns {"success": True} or {"success": False, "message": str, "content_version_id": str}.
    Reads db and db_application_id from the logging context (set by run_persisted_step)
    so that events are written to the application timeline automatically.
    """
    db, db_application_id = get_db_context()
    total = len(content_version_ids)
    logger.info("[WORKFLOW] Starting Salesforce download — %s file(s)", total)
    log_application_event(
        db, db_application_id, EVT_SF_DOWNLOAD_STARTED,
        "Downloading %s file(s) from Salesforce" % total,
        {"count": total, "content_version_ids": content_version_ids})

    for cv_id in content_version_ids:
        filename = f"{cv_id}.pdf"
        try:
            logger.info("[WORKFLOW] Downloading ContentVersion id=%s from Salesforce", cv_id)
            file_bytes = download_content_version(cv_id)

            if not file_bytes.startswith(b"%PDF"):
                msg = "ContentVersion %s did not return a valid PDF" % cv_id
                log_application_event(
                    db, db_application_id, EVT_SF_DOWNLOAD_FAILED,
                    msg,
                    {"content_version_id": cv_id})
                return {
                    "success": False,
                    "message": msg,
                    "content_version_id": cv_id}

            save_downloaded_documents(
                folder_path=folder_path,
                documents=[{
                    "filename": filename,
                    "bytes": file_bytes,
                    "document_type": "bank_statement"}])

            logger.info(
                "[WORKFLOW] Saved ContentVersion id=%s as %s (%s bytes)",
                cv_id, filename, len(file_bytes))

        except Exception as exc:
            msg = "Failed to download ContentVersion %s from Salesforce: %s" % (cv_id, exc)
            logger.error("[WORKFLOW] Failed to download ContentVersion id=%s: %s", cv_id, exc)
            log_application_event(
                db, db_application_id, EVT_SF_DOWNLOAD_FAILED,
                msg,
                {"content_version_id": cv_id, "error": str(exc)})
            return {
                "success": False,
                "message": msg,
                "content_version_id": cv_id}

    log_application_event(
        db, db_application_id, EVT_SF_DOWNLOAD_DONE,
        "All %s Salesforce file(s) downloaded successfully" % total,
        {"count": total})
    logger.info("[WORKFLOW] All %s Salesforce file(s) downloaded successfully", total)
    return {"success": True}


def step_submit_bank_statements(session: requests.Session, salesforce_payload: dict, application_id: str):
    system = salesforce_payload.get("system") or {}
    processing_files_dir = system.get("processing_files_dir")
    action_type = system.get("bank_statements_action_type")

    # -----------------------------------------------------------------------
    # Step 1: Download files from Salesforce if content_version_ids are present
    # and the processing folder does not yet contain any files.
    # This replaces the old base64/multipart upload approach.
    # -----------------------------------------------------------------------
    content_version_ids = salesforce_payload.get("content_version_ids") or []

    if content_version_ids:
        # Ensure the processing folder exists. At this point we know the FC
        # application_id, so we use it as the folder name.
        if not processing_files_dir:
            processing_files_dir = get_or_create_application_folder(application_id)
            system["processing_files_dir"] = processing_files_dir
            salesforce_payload["system"] = system
            logger.info(
                "[WORKFLOW] Created processing folder for application_id=%s at %s",
                application_id, processing_files_dir)

        existing_files = list_documents_by_type(processing_files_dir, "bank_statement")

        if not existing_files:
            logger.info(
                "[WORKFLOW] Fetching %s file(s) from Salesforce for application_id=%s",
                len(content_version_ids), application_id)

            fetch_result = _fetch_files_from_salesforce(content_version_ids, processing_files_dir)

            if not fetch_result["success"]:
                return {
                    "success": False,
                    "step": "submit_bank_statements",
                    "stage": "salesforce_download",
                    "application_id": application_id,
                    "salesforce_payload": salesforce_payload,
                    "message": fetch_result["message"],
                    "retryable": True}
        else:
            logger.info(
                "[WORKFLOW] Processing folder already contains %s file(s) — skipping Salesforce download",
                len(existing_files))

    # -----------------------------------------------------------------------
    # Step 2: Determine the bank statement action type if not already set
    # -----------------------------------------------------------------------
    if not action_type:
        next_action_response = perform_next_step(session=session, application_id=application_id)
        action_type = next_action_response.get("type")
        logger.info("[WORKFLOW] Bank statement action_type resolved from next_action: %s", action_type)

    if action_type not in {"get_bank_statements", "amend_bank_statements", "parse_bank_statements"}:
        logger.error("[WORKFLOW] Unexpected bank statement action: %s", action_type)
        return {
            "success": False,
            "step": "submit_bank_statements",
            "stage": "submit_bank_statements",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": "Unexpected bank statement action: %s" % action_type,
            "retryable": False}

    # -----------------------------------------------------------------------
    # Step 3: Upload each PDF to FC via presigned S3 URL
    # -----------------------------------------------------------------------
    bank_statement_files = list_documents_by_type(processing_files_dir, "bank_statement")
    logger.info("[WORKFLOW] Found %s bank statement file(s) to upload", len(bank_statement_files))

    if not bank_statement_files:
        logger.error("[WORKFLOW] No bank statement PDFs found in processing folder: %s", processing_files_dir)
        return {
            "success": False,
            "step": "submit_bank_statements",
            "stage": "submit_bank_statements",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": "No bank statement PDF files were found for upload",
            "retryable": False}

    uploaded_bank_statement_documents = []

    for file_path in bank_statement_files:
        filename = file_path.split("/")[-1]

        logger.info("[WORKFLOW] Uploading bank statement: %s", filename)
        presigned_result = get_presigned_upload_url(session=session, owner_id=application_id, filename=filename)

        upload_url = presigned_result.get("url")
        s3_key = presigned_result.get("s3_key")

        if not upload_url or not s3_key:
            logger.error("[WORKFLOW] Missing upload url or s3_key for %s", filename)
            return {
                "success": False,
                "step": "submit_bank_statements",
                "stage": "submit_bank_statements",
                "application_id": application_id,
                "salesforce_payload": salesforce_payload,
                "message": "Missing upload url or s3_key for %s" % filename,
                "retryable": True}

        upload_document_to_s3(upload_url=upload_url, file_path=file_path)
        logger.info("[WORKFLOW] Uploaded %s to S3", filename)

        document_result = create_document(
            session=session,
            application_id=application_id,
            owner_id=application_id,
            filename=filename,
            s3_key=s3_key,
            document_type="bank_statement")
        logger.info("[WORKFLOW] Registered document %s document_id=%s", filename, document_result.get("id"))

        uploaded_bank_statement_documents.append({
            "filename": filename,
            "s3_key": s3_key,
            "document_result": document_result})

    system["uploaded_bank_statement_documents"] = uploaded_bank_statement_documents
    salesforce_payload["system"] = system

    # -----------------------------------------------------------------------
    # Step 4: Submit the bank statements action to FC
    # -----------------------------------------------------------------------
    logger.info("[WORKFLOW] Submitting bank statements action=%s application_id=%s", action_type, application_id)
    amend_bank_statements(session=session, application_id=application_id)

    next_action_result = poll_until_state(
        session=session,
        application_id=application_id,
        target_state={"identify_executive_business_owners", "application_submitted", "amend_bank_statements", "parse_bank_statements"},
        interval_seconds=3,
        max_attempts=20)

    if not next_action_result["success"]:
        return {
            "success": False,
            "step": "submit_bank_statements",
            "stage": "next_action_polling",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "message": next_action_result.get("error"),
            "next_action_result": next_action_result,
            "retryable": next_action_result.get("final_state") == "timeout"}

    resolved_state = next_action_result["response"].get("type")
    logger.info("[WORKFLOW] Bank statements resolved next state=%s application_id=%s", resolved_state, application_id)

    if resolved_state in {"amend_bank_statements", "parse_bank_statements"}:
        salesforce_payload["system"]["bank_statements_action_type"] = resolved_state
        return {
            "success": True,
            "step": "submit_bank_statements",
            "stage": "submit_bank_statements",
            "application_id": application_id,
            "salesforce_payload": salesforce_payload,
            "retryable": False}

    return {
        "success": True,
        "step": resolved_state,
        "stage": resolved_state,
        "application_id": application_id,
        "salesforce_payload": salesforce_payload,
        "retryable": False}