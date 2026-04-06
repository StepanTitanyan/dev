import time
import json
import logging
import requests
from pathlib import Path
from rise.config.config import settings

logger = logging.getLogger(__name__)

_AUTH_BASE = settings.FC_AUTH_BASE_URL
_API_BASE = settings.FC_API_BASE_URL
_WEB_BASE = settings.FC_WEB_BASE_URL


class FundingCircleIneligibleError(Exception):
    """Raised when FC rejects an application for a permanent business reason."""
    def __init__(self, message: str, errors: dict):
        super().__init__(message)
        self.errors = errors


class FundingCircleValidationError(Exception):
    """Raised when FC returns a 422 for a fixable validation issue."""
    def __init__(self, message: str, errors: dict):
        super().__init__(message)
        self.errors = errors


# Permanent rejection error keys — these should never be retried
_PERMANENT_REJECTION_KEYS = {
    "user_has_in_flight_loan_application",
    "ineligible_for_gpo",
    "company_not_eligible",
    "broker_not_eligible",
}


def _log_outgoing_payload(endpoint_name: str, payload: dict, application_id: str | None = None):
    # DEBUG level — these payloads contain personal data (names, DOBs, addresses,
    # financial figures). Keeping them out of INFO logs avoids flooding CloudWatch
    # and reduces the amount of personal data stored in log archives.
    # Set LOG_LEVEL=DEBUG locally when you need to inspect payloads.
    if application_id:
        logger.debug(
            "[CLIENT:PAYLOAD] endpoint=%s application_id=%s payload=%s",
            endpoint_name,
            application_id,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        logger.debug(
            "[CLIENT:PAYLOAD] endpoint=%s payload=%s",
            endpoint_name,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _log_response(
    endpoint_name: str,
    response: requests.Response,
    application_id: str | None = None,
    elapsed_s: float | None = None
):
    level = logging.INFO if response.ok else logging.ERROR
    content_type = response.headers.get("Content-Type", "")

    if "text/html" in content_type:
        body_summary = "<HTML response %s bytes>" % len(response.content)
    else:
        body_summary = response.text[:500] if response.text else ""

    elapsed_info = " elapsed=%ss" % elapsed_s if elapsed_s is not None else ""

    logger.log(
        level,
        "[CLIENT:RESPONSE] endpoint=%s application_id=%s status=%s%s body=%s",
        endpoint_name,
        application_id or "—",
        response.status_code,
        elapsed_info,
        body_summary)


def _raise_with_log(endpoint_name: str, response: requests.Response, application_id: str | None = None):
    if not response.ok:
        logger.error(
            "[CLIENT:ERROR] endpoint=%s application_id=%s status=%s body=%s",
            endpoint_name,
            application_id or "—",
            response.status_code,
            response.text[:2000] if response.text else "")

        if response.status_code == 422:
            try:
                body = response.json()
                errors = body.get("errors", {})

                permanent_keys = set(errors.keys()) & _PERMANENT_REJECTION_KEYS
                if permanent_keys:
                    messages = []
                    for key in permanent_keys:
                        messages.extend(errors[key])
                    raise FundingCircleIneligibleError(
                        "FC permanently rejected application: %s" % "; ".join(messages),
                        errors=errors)

                raise FundingCircleValidationError(
                    "FC validation error at %s: %s" % (endpoint_name, errors),
                    errors=errors)
            except (ValueError, KeyError):
                pass

        response.raise_for_status()


def auth_login(session: requests.Session, username: str, password: str):
    url = _AUTH_BASE + "/v1/initiate_auth"
    payload = {
        "username": username,
        "password": password,
        "requestSource": "LOGIN"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"}

    logger.info("[CLIENT] auth_login — attempting login for username=%s", username)
    _start = time.time()
    response = session.post(url, json=payload, headers=headers, timeout=20)
    _log_response("auth_login", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("auth_login", response)
    return response.json()


def auth_otp(session: requests.Session, session_token: str, username: str, sms_code: str):
    url = _AUTH_BASE + "/v1/mfa_entry"
    payload = {
        "session": session_token,
        "username": username,
        "smsCode": sms_code,
        "trustDevice": "false"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"}

    logger.info("[CLIENT] auth_otp — submitting OTP for username=%s", username)
    _start = time.time()
    response = session.post(url, json=payload, headers=headers, timeout=20)
    _log_response("auth_otp", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("auth_otp", response)
    return response.json()


def oauth_session_bridge(session: requests.Session):
    url = _WEB_BASE + "/auth/funding_circle_oauth2?origin=%2Fintroducers%2Fsummary"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": _WEB_BASE + "/uk/auth/login",
        "User-Agent": "Mozilla/5.0"}

    logger.info("[CLIENT] oauth_session_bridge — bridging OAuth session")
    _start = time.time()
    response = session.get(url, headers=headers, allow_redirects=True, timeout=20)
    _log_response("oauth_session_bridge", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("oauth_session_bridge", response)
    return response


def _get_csrf_token(session: requests.Session):
    for c in session.cookies:
        if c.name == "XSRF-TOKEN":
            return c.value
    logger.warning("[CLIENT] CSRF token not found in session cookies")
    return None


def open_introducers_summary(session: requests.Session):
    url = _WEB_BASE + "/introducers/summary"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0"}

    logger.info("[CLIENT] open_introducers_summary")
    _start = time.time()
    response = session.get(url, headers=headers, allow_redirects=True, timeout=20)
    _log_response("open_introducers_summary", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("open_introducers_summary", response)
    return response


def summary_term_loan_bootstrap(session: requests.Session):
    url = _WEB_BASE + "/introducers/summary?tab=term-loan"
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Referer": _WEB_BASE + "/introducers/summary",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    logger.info("[CLIENT] summary_term_loan_bootstrap")
    _start = time.time()
    response = session.get(url, headers=headers, timeout=20)
    _log_response("summary_term_loan_bootstrap", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("summary_term_loan_bootstrap", response)
    return response.json()


def broker_me(session: requests.Session):
    url = _API_BASE + "/api/v1/broker/me"
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "*/*",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    logger.info("[CLIENT] broker_me — validating broker session")
    _start = time.time()
    response = session.get(url, headers=headers, timeout=20)
    _log_response("broker_me", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("broker_me", response)
    return response.json()


def company_search(session: requests.Session, search_string: str, company_type: str = "limited"):
    url = _API_BASE + "/api/v1/company_search"
    params = {"search_string": search_string, "type": company_type}
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    logger.info("[CLIENT] company_search — search_string=%s type=%s", search_string, company_type)
    _start = time.time()
    response = session.get(url, params=params, headers=headers, timeout=20)
    _log_response("company_search", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("company_search", response)
    result = response.json()
    logger.info("[CLIENT] company_search — returned %s results", len(result) if isinstance(result, list) else "?")
    return result


def eligibility_check(session: requests.Session, payload: dict):
    url = _API_BASE + "/api/v1/brokers/eligibility_checks"
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    logger.info("[CLIENT] eligibility_check — company=%s amount_cents=%s",
                payload.get("company_name"), payload.get("amount_requested_cents"))
    _log_outgoing_payload("eligibility_check", payload)
    _start = time.time()
    response = session.post(url, json=payload, headers=headers, timeout=20)
    _log_response("eligibility_check", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("eligibility_check", response)
    return response.json()


def get_applicant_details(session: requests.Session, payload: dict, application_id: str):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/get_applicant_details" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("get_applicant_details", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("get_applicant_details", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("get_applicant_details", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def get_loan_application_details(session: requests.Session, payload: dict, application_id: str):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/get_loan_application_details" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("get_loan_application_details", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("get_loan_application_details", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("get_loan_application_details", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def get_company_performance_details(session: requests.Session, payload: dict, application_id: str):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/get_company_performance_details" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("get_company_performance_details", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("get_company_performance_details", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("get_company_performance_details", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def get_contact_details(session: requests.Session, payload: dict, application_id: str):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/get_contact_details" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("get_contact_details", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("get_contact_details", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("get_contact_details", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def select_executive_business_owners(session: requests.Session, payload: dict, application_id: str):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/select_executive_business_owners" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("select_executive_business_owners", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("select_executive_business_owners", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("select_executive_business_owners", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def perform_next_step(session: requests.Session, application_id: str):
    url = _API_BASE + "/api/v1/brokers/loan_applications/%s/next_action" % application_id
    csrf_token = _get_csrf_token(session)
    headers = {
        "Accept": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    logger.debug("[CLIENT] perform_next_step — application_id=%s", application_id)
    _start = time.time()
    response = session.get(url, headers=headers, timeout=20)
    _log_response("perform_next_step", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("perform_next_step", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def get_presigned_upload_url(session: requests.Session, owner_id: str, filename: str):
    url = _API_BASE + "/api/v1/presigned_upload_url/new"
    csrf_token = _get_csrf_token(session)
    params = {"owner_id": owner_id, "filename": filename}
    headers = {
        "Accept": "*/*",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    logger.info("[CLIENT] get_presigned_upload_url — owner_id=%s filename=%s", owner_id, filename)
    _start = time.time()
    response = session.get(url, params=params, headers=headers, timeout=20)
    _log_response("get_presigned_upload_url", response, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("get_presigned_upload_url", response)
    return response.json()


def upload_document_to_s3(upload_url: str, file_path: str | Path):
    path = Path(file_path)
    file_size = path.stat().st_size
    headers = {
        "Accept": "*/*",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "x-amz-server-side-encryption": "aws:kms"}

    logger.info("[CLIENT] upload_document_to_s3 — file=%s size=%s bytes", path.name, file_size)
    _start = time.time()
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "application/pdf")}
        response = requests.put(upload_url, headers=headers, files=files, timeout=120)
    elapsed = round(time.time() - _start, 3)
    _log_response("upload_document_to_s3", response, elapsed_s=elapsed)
    _raise_with_log("upload_document_to_s3", response)
    logger.info("[CLIENT] upload_document_to_s3 — completed file=%s in %ss", path.name, elapsed)
    return {"success": True, "status_code": response.status_code, "body": response.text}


def create_document(
    session: requests.Session,
    application_id: str,
    owner_id: str,
    filename: str,
    s3_key: str,
    document_type: str = "bank_statement"
):
    url = _API_BASE + "/api/v1/loan_applications/%s/documents" % application_id
    csrf_token = _get_csrf_token(session)
    payload = {
        "owner_id": owner_id,
        "owner_type": "loan_application",
        "filename": filename,
        "s3_key": s3_key,
        "document_type": document_type}
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("create_document", payload, application_id)
    _start = time.time()
    response = session.post(url, json=payload, headers=headers, timeout=20)
    _log_response("create_document", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("create_document", response, application_id)
    return response.json()


def amend_bank_statements(session: requests.Session, application_id: str, document_type: str = "bank_statement"):
    url = _API_BASE + "/api/v1/loan_applications/%s/actions/amend_bank_statements" % application_id
    csrf_token = _get_csrf_token(session)
    payload = {"payload": {"document_type": document_type}}
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": _WEB_BASE,
        "Referer": _WEB_BASE + "/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest"}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
        headers["X-XSRF-TOKEN"] = csrf_token

    _log_outgoing_payload("amend_bank_statements", payload, application_id)
    _start = time.time()
    response = session.patch(url, json=payload, headers=headers, timeout=20)
    _log_response("amend_bank_statements", response, application_id, elapsed_s=round(time.time() - _start, 3))
    _raise_with_log("amend_bank_statements", response, application_id)
    if response.status_code == 204 or not response.text.strip():
        return {"success": True, "status_code": response.status_code, "body": None}
    return response.json()


def upload_bank_statement(session: requests.Session, application_id: str, file_path: str | Path):
    """
    Convenience wrapper used by manual test scripts.
    The main worker calls each step individually via the workflow engine.
    """
    path = Path(file_path)
    logger.info("[CLIENT] upload_bank_statement — application_id=%s file=%s", application_id, path.name)
    presigned = get_presigned_upload_url(session, application_id, path.name)
    upload_url = presigned["url"]
    s3_key = presigned["s3_key"]
    upload_document_to_s3(upload_url, path)
    document = create_document(session, application_id, application_id, path.name, s3_key, "bank_statement")
    next_action = amend_bank_statements(session, application_id)
    return {"presigned": presigned, "document": document, "next_action": next_action}