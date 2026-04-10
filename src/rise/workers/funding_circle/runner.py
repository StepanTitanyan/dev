import json
import time
import logging
import requests

from rise.config.config import USERNAME, PASSWORD, OTP_WAIT_SECONDS, settings, setup_logging
from rise.workers.funding_circle.client import auth_login, auth_otp, oauth_session_bridge, open_introducers_summary, summary_term_loan_bootstrap, broker_me, FundingCircleIneligibleError, FundingCircleValidationError
from rise.workers.funding_circle.parsing import parse_initiate_auth_response, parse_auth_result
from rise.db.repositories import (
    get_application_by_id,
    get_next_processible_application,
    update_application_status,
    mark_application_for_retry,
    set_external_id,
    update_working_payload,
    set_worker_waiting_for_otp,
    mark_worker_authenticated,
    invalidate_worker_auth,
    get_latest_worker_otp,
    consume_latest_worker_otp,
    reset_stuck_processing_applications,
    log_application_event,
    EVT_PROCESSING_STARTED, EVT_COMPLETED, EVT_PARTIALLY_COMPLETED,
    EVT_FAILED, EVT_RETRY_SCHEDULED, EVT_FC_REJECTED,
    EVT_FC_VALIDATION_ERROR, EVT_WORKER_ERROR)
from rise.db.session import SessionLocal
from rise.queue.sqs import enqueue_application_job
from rise.workers.funding_circle.workflow import run_application_workflow
from rise.logging_context import set_log_context, clear_log_context

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5

# How long an application can sit in "processing" before we consider the
# worker dead and reset it back to "queued".
STUCK_PROCESSING_THRESHOLD_MINUTES = 30

# How often to log a heartbeat so CloudWatch can alert if the worker goes silent.
HEARTBEAT_INTERVAL_SECONDS = 300  # every 5 minutes

# SQS visibility timeout — must be longer than the longest possible workflow run.
# 15 minutes covers login + OTP wait + all FC steps comfortably.
SQS_VISIBILITY_TIMEOUT_SECONDS = 900


def get_retry_delay_seconds(retry_count: int):
    retry_schedule = {
        0: 30,
        1: 120,
        2: 600}
    return retry_schedule.get(retry_count, 1800)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def _validate_config():
    """
    Checks all required env vars at startup and raises immediately if any are
    missing. Prevents the worker from starting in a broken state where it would
    process applications for minutes before failing on a missing credential.
    """
    errors = []

    if not settings.POSTGRES_HOST:
        errors.append("POSTGRES_HOST is not set")
    if not USERNAME:
        errors.append("APP_USERNAME is not set")
    if not PASSWORD:
        errors.append("APP_PASSWORD is not set")
    if settings.ENABLE_SQS and not settings.SQS_QUEUE_URL:
        errors.append("SQS_QUEUE_URL is required when ENABLE_SQS=true")

    # Salesforce credentials are only required if any application will need
    # bank statement downloads. We warn rather than hard-fail here because
    # not every application reaches submit_bank_statements.
    if not settings.SALESFORCE_INSTANCE_URL:
        logger.warning("[INITIALIZE] SALESFORCE_INSTANCE_URL is not set — bank statement downloads will fail")
    if not settings.SALESFORCE_CLIENT_ID:
        logger.warning("[INITIALIZE] SALESFORCE_CLIENT_ID is not set — bank statement downloads will fail")
    if not settings.SALESFORCE_CLIENT_SECRET:
        logger.warning("[INITIALIZE] SALESFORCE_CLIENT_SECRET is not set — bank statement downloads will fail")

    if errors:
        for error in errors:
            logger.error("[INITIALIZE] Config error: %s", error)
        raise RuntimeError(
            "Worker cannot start — missing required configuration: %s" % errors)

    logger.info("[INITIALIZE] Config validation passed")


# ---------------------------------------------------------------------------
# Stuck application recovery
# ---------------------------------------------------------------------------

def _recover_stuck_applications():
    """
    At startup, reset any applications that were left in 'processing' status
    from a previous worker run that crashed mid-flight. Without this, those
    applications would never be retried.
    """
    db = SessionLocal()
    try:
        count = reset_stuck_processing_applications(
            db, stuck_after_minutes=STUCK_PROCESSING_THRESHOLD_MINUTES)
        if count > 0:
            logger.warning(
                "[INITIALIZE] Reset %s stuck 'processing' application(s) back to 'queued' "
                "(were stuck for >%s minutes — previous worker likely crashed)",
                count, STUCK_PROCESSING_THRESHOLD_MINUTES)
        else:
            logger.info("[INITIALIZE] No stuck applications found")
    except Exception as exc:
        logger.exception("[INITIALIZE] Failed to recover stuck applications: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Periodic stuck check (runs inside the poll loop)
# ---------------------------------------------------------------------------

_last_stuck_check: float = 0.0

def _maybe_check_for_stuck_applications():
    """
    Periodically checks for stuck applications during normal operation, not
    just at startup. Handles the case where the worker becomes healthy again
    after a period of failures and needs to recover mid-flight applications.
    """
    global _last_stuck_check
    now = time.time()
    # Run every STUCK_PROCESSING_THRESHOLD_MINUTES
    if now - _last_stuck_check < STUCK_PROCESSING_THRESHOLD_MINUTES * 60:
        return
    _last_stuck_check = now

    db = SessionLocal()
    try:
        count = reset_stuck_processing_applications(
            db, stuck_after_minutes=STUCK_PROCESSING_THRESHOLD_MINUTES)
        if count > 0:
            logger.warning(
                "[INITIALIZE] Periodic check: reset %s stuck application(s) back to 'queued'",
                count)
    except Exception as exc:
        logger.exception("[INITIALIZE] Periodic stuck check failed: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

_last_heartbeat: float = 0.0

def _maybe_log_heartbeat():
    """
    Logs a heartbeat line periodically so CloudWatch can alert if it goes
    silent. A missing heartbeat means the worker has died or is hanging.
    """
    global _last_heartbeat
    now = time.time()
    if now - _last_heartbeat < HEARTBEAT_INTERVAL_SECONDS:
        return
    _last_heartbeat = now
    logger.info(
        "[INITIALIZE] ♥ worker alive — mode=%s",
        "SQS" if settings.ENABLE_SQS else "DB poll")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def is_session_valid(session: requests.Session):
    try:
        logger.info("[INITIALIZE] Validating existing worker session")
        broker_me(session)
        logger.info("[INITIALIZE] Existing worker session is valid")
        return True
    except Exception:
        logger.warning("[INITIALIZE] Existing worker session is invalid")
        return False


def wait_for_otp_from_db(timeout_seconds: int):
    started_at = time.time()
    logger.info("[INITIALIZE] Polling database for OTP for up to %s seconds", timeout_seconds)

    while time.time() - started_at < timeout_seconds:
        db = SessionLocal()
        try:
            otp_code = get_latest_worker_otp(db, "funding_circle")
            if otp_code:
                logger.info("[INITIALIZE] OTP found in database")
                consumed_otp = consume_latest_worker_otp(db, "funding_circle")
                logger.info("[INITIALIZE] OTP consumed from database")
                return consumed_otp
        finally:
            db.close()

        time.sleep(2)

    logger.warning("[INITIALIZE] OTP was not received within %s second timeout", timeout_seconds)
    return None


def login_and_bootstrap(session: requests.Session):
    logger.info("[INITIALIZE] Logging in worker session...")

    # Clear any stale unconsumed OTPs from previous login attempts.
    # Submitting an old OTP with a new Cognito session token causes 401.
    db = SessionLocal()
    try:
        from rise.db.models import OtpMessage
        stale = (
            db.query(OtpMessage)
            .filter(OtpMessage.service == "funding_circle", OtpMessage.status == "received")
            .all()
        )
        for otp in stale:
            otp.status = "expired"
        if stale:
            db.commit()
            logger.info("[INITIALIZE] Cleared %s stale OTP(s) before new login", len(stale))
    except Exception as exc:
        logger.warning("[INITIALIZE] Failed to clear stale OTPs: %s", exc)
    finally:
        db.close()

    try:
        login_response = auth_login(session, USERNAME, PASSWORD)
    except Exception as exc:
        if hasattr(exc, "response") and exc.response is not None and exc.response.status_code == 429:
            logger.warning("[INITIALIZE] FC auth rate limited (429) — sleeping 60s before retry")
            time.sleep(60)
        raise
    parsed = parse_initiate_auth_response(login_response)

    logger.info("[INITIALIZE] Login challenge type: %s", parsed["challenge_name"])
    if parsed["challenge_name"] != "SMS_MFA":
        raise RuntimeError("Unexpected challenge type: %s" % parsed["challenge_name"])

    db = SessionLocal()
    try:
        set_worker_waiting_for_otp(db, "funding_circle", auth_session_token=parsed.get("session"))
    finally:
        db.close()

    logger.info("[INITIALIZE] Waiting for OTP for up to %s seconds...", OTP_WAIT_SECONDS)
    otp_code = wait_for_otp_from_db(timeout_seconds=OTP_WAIT_SECONDS)
    if not otp_code:
        raise TimeoutError("No OTP received within %s second timeout." % OTP_WAIT_SECONDS)

    logger.info("[INITIALIZE] OTP received, submitting...")
    otp_response = auth_otp(session, parsed["session"], USERNAME, otp_code)
    otp_parsed = parse_auth_result(otp_response)
    logger.info("[INITIALIZE] OTP auth completed is_authenticated=%s", otp_parsed.get("is_authenticated"))

    logger.info("[INITIALIZE] Running OAuth/session bootstrap...")
    oauth_session_bridge(session)
    logger.info("[INITIALIZE] OAuth session bridge completed")
    open_introducers_summary(session)
    logger.info("[INITIALIZE] Introducers summary opened")
    summary_term_loan_bootstrap(session)
    logger.info("[INITIALIZE] Term loan bootstrap completed")
    broker_me(session)
    logger.info("[INITIALIZE] broker_me validation completed")

    db = SessionLocal()
    try:
        mark_worker_authenticated(db, "funding_circle")
    finally:
        db.close()

    logger.info("[INITIALIZE] Worker session fully authenticated and ready")
    return True


def ensure_authenticated(session: requests.Session):
    logger.info("[INITIALIZE] Ensuring worker is authenticated")
    if is_session_valid(session):
        return True

    db = SessionLocal()
    try:
        invalidate_worker_auth(db, "funding_circle", error_message="Session missing or expired. Re-authenticating.")
    finally:
        db.close()

    logger.info("[INITIALIZE] Re-authentication required")
    login_and_bootstrap(session)
    return True


# ---------------------------------------------------------------------------
# Application processing
# ---------------------------------------------------------------------------

def _is_already_processing(application_id: int) -> bool:
    """
    Guard against double-processing. If the application is already in
    'processing' status and was updated recently (within the stuck threshold),
    another worker instance or a race condition is handling it — skip it.
    """
    db = SessionLocal()
    try:
        application = get_application_by_id(db, application_id)
        if not application:
            logger.warning("[INITIALIZE] Application not found application_id=%s", application_id)
            return

        if application.status in {"failed", "rejected", "completed", "partially_completed"}:
            logger.info(
                "[INITIALIZE] Skipping application_id=%s — already in terminal status=%s",
                application_id, application.status)
            return

        if application.status != "processing":
            return False

        # If it's been processing for less than the stuck threshold,
        # something else is actively working on it — don't touch it.
        from datetime import timezone
        from datetime import datetime
        now = datetime.now(timezone.utc)
        if application.updated_at:
            updated_at = application.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            minutes_processing = (now - updated_at).total_seconds() / 60
            if minutes_processing < STUCK_PROCESSING_THRESHOLD_MINUTES:
                logger.warning(
                    "[INITIALIZE] Application application_id=%s is already in 'processing' "
                    "state (for %.1f minutes) — skipping to avoid double-processing",
                    application_id, minutes_processing)
                return True

        return False
    finally:
        db.close()


def process_application(application_id: int, session: requests.Session):
    # Double-processing guard — if already being processed by another run,
    # skip rather than submitting the same application to FC twice.
    if _is_already_processing(application_id):
        return

    logger.info("[INITIALIZE] Starting processing for application_id=%s", application_id)
    db = SessionLocal()
    try:
        application = get_application_by_id(db, application_id)
        if not application:
            logger.warning("[INITIALIZE] Application not found application_id=%s", application_id)
            return

        tracking_id = application.tracking_id
        salesforce_record_id = application.salesforce_record_id

        # Set log context — from this point every log line in the worker,
        # workflow, client, and matching modules automatically carries
        # tracking_id, salesforce_record_id, and (once known) fc_application_id.
        set_log_context(
            tracking_id=tracking_id,
            salesforce_record_id=salesforce_record_id or "",
            fc_application_id=application.external_id or "")

        logger.info(
            "[INITIALIZE] Processing application_id=%s tracking_id=%s salesforce_record_id=%s step=%s",
            application_id, tracking_id, salesforce_record_id,
            application.current_step or "eligibility_check")

        update_application_status(
            db=db,
            application=application,
            status="processing",
            current_step=application.current_step,
            last_error=None)

        log_application_event(
            db, application_id, EVT_PROCESSING_STARTED,
            "Worker picked up application — step=%s" % (application.current_step or "eligibility_check"),
            {"step": application.current_step or "eligibility_check",
             "retry_count": application.retry_count})

        workflow_start = time.time()

        result = run_application_workflow(
            salesforce_payload=application.working_payload_json or application.raw_input_json,
            step=application.current_step or "eligibility_check",
            session=session,
            application_id=application.external_id,
            db=db,
            db_application_id=application.id)

        elapsed = round(time.time() - workflow_start, 2)

        application = get_application_by_id(db, application_id)
        if not application:
            logger.warning("[INITIALIZE] Application disappeared during processing application_id=%s", application_id)
            return

        logger.info(
            "[INITIALIZE] Workflow returned application_id=%s tracking_id=%s "
            "success=%s step=%s retryable=%s elapsed=%ss",
            application_id, tracking_id,
            result.get("success"), result.get("step"),
            result.get("retryable"), elapsed)

        returned_fc_application_id = result.get("application_id")
        if returned_fc_application_id and not application.external_id:
            set_external_id(db, application, returned_fc_application_id)
            application = get_application_by_id(db, application_id)

        returned_payload = result.get("salesforce_payload")
        if returned_payload:
            update_working_payload(db, application, returned_payload)
            application = get_application_by_id(db, application_id)

        if result.get("success"):
            completion_status = result.get("completion_status")

            if completion_status == "partially_successful":
                update_application_status(
                    db=db,
                    application=application,
                    status="partially_completed",
                    current_step=result.get("step"),
                    last_error=None)
                logger.info(
                    "[INITIALIZE] ✓ Application partially completed "
                    "application_id=%s tracking_id=%s salesforce_record_id=%s "
                    "fc_application_id=%s step=%s elapsed=%ss",
                    application_id, tracking_id, salesforce_record_id,
                    application.external_id, result.get("step"), elapsed)
                log_application_event(
                    db, application_id, EVT_PARTIALLY_COMPLETED,
                    "Application partially completed in %ss — FC will contact for shareholder info" % elapsed,
                    {"fc_application_id": application.external_id,
                     "step": result.get("step"), "elapsed_s": elapsed})
                return

            update_application_status(
                db=db,
                application=application,
                status="completed",
                current_step=result.get("step"),
                last_error=None)
            logger.info(
                "[INITIALIZE] ✓ Application completed successfully "
                "application_id=%s tracking_id=%s salesforce_record_id=%s "
                "fc_application_id=%s step=%s elapsed=%ss",
                application_id, tracking_id, salesforce_record_id,
                application.external_id, result.get("step"), elapsed)
            log_application_event(
                db, application_id, EVT_COMPLETED,
                "Application submitted to FundingCircle successfully in %ss" % elapsed,
                {"fc_application_id": application.external_id,
                 "step": result.get("step"), "elapsed_s": elapsed})
            return

        # --- failure path ---
        error_message = result.get("message") or "Unknown workflow failure"
        retryable = result.get("retryable", False)
        retry_delay_seconds = get_retry_delay_seconds(application.retry_count)

        if retryable and application.retry_count < application.max_retries:
            mark_application_for_retry(
                db=db,
                application=application,
                current_step=application.current_step,
                last_error=error_message,
                delay_seconds=retry_delay_seconds)
            logger.warning(
                "[INITIALIZE] ↺ Application queued for retry "
                "application_id=%s tracking_id=%s salesforce_record_id=%s "
                "retry=%s/%s delay=%ss error=%s",
                application_id, tracking_id, salesforce_record_id,
                application.retry_count + 1, application.max_retries,
                retry_delay_seconds, error_message)
            log_application_event(
                db, application_id, EVT_RETRY_SCHEDULED,
                "Retry %s/%s scheduled in %ss — %s" % (
                    application.retry_count + 1, application.max_retries,
                    retry_delay_seconds, error_message),
                {"retry_count": application.retry_count + 1,
                 "max_retries": application.max_retries,
                 "delay_s": retry_delay_seconds,
                 "error": error_message,
                 "step": application.current_step})
        else:
            is_rejected = result.get("rejected", False)
            final_status = "rejected" if is_rejected else "failed"

            update_application_status(
                db=db,
                application=application,
                status=final_status,
                current_step=application.current_step,
                last_error=error_message)
            logger.error(
                "[INITIALIZE] ✗ Application permanently %s "
                "application_id=%s tracking_id=%s salesforce_record_id=%s "
                "fc_application_id=%s retryable=%s retry_count=%s error=%s",
                final_status,
                application_id, tracking_id, salesforce_record_id,
                application.external_id,
                retryable, application.retry_count, error_message)
            log_application_event(
                db, application_id, EVT_FAILED,
                "Application %s — %s" % (final_status, error_message),
                {"error": error_message, "retryable": retryable,
                 "retry_count": application.retry_count,
                 "step": application.current_step,
                 "fc_application_id": application.external_id})

    except FundingCircleIneligibleError as exc:
        logger.error(
            "[INITIALIZE] ✗ FC permanently rejected application "
            "application_id=%s tracking_id=%s reason=%s errors=%s",
            application_id,
            locals().get("tracking_id", "unknown"),
            str(exc), exc.errors)
        application = get_application_by_id(db, application_id)
        if application:
            update_application_status(
                db=db,
                application=application,
                status="failed",
                current_step=application.current_step,
                last_error=str(exc))
            log_application_event(
                db, application_id, EVT_FC_REJECTED,
                "FundingCircle permanently rejected — %s" % str(exc),
                {"errors": exc.errors, "step": application.current_step})

    except FundingCircleValidationError as exc:
        logger.error(
            "[INITIALIZE] ✗ FC validation error — will not retry "
            "application_id=%s tracking_id=%s errors=%s",
            application_id,
            locals().get("tracking_id", "unknown"),
            exc.errors)
        application = get_application_by_id(db, application_id)
        if application:
            update_application_status(
                db=db,
                application=application,
                status="failed",
                current_step=application.current_step,
                last_error="FC validation error: %s" % exc.errors)
            log_application_event(
                db, application_id, EVT_FC_VALIDATION_ERROR,
                "FundingCircle validation error — %s" % exc.errors,
                {"errors": exc.errors, "step": application.current_step})

    except Exception as exc:
        logger.exception(
            "[INITIALIZE] ✗ Unhandled exception "
            "application_id=%s tracking_id=%s error=%s: %s",
            application_id,
            locals().get("tracking_id", "unknown"),
            type(exc).__name__, exc)

        application = get_application_by_id(db, application_id)
        if application:
            retry_delay_seconds = get_retry_delay_seconds(application.retry_count)
            error_message = "%s: %s" % (type(exc).__name__, exc)

            if application.retry_count < application.max_retries:
                mark_application_for_retry(
                    db=db,
                    application=application,
                    current_step=application.current_step,
                    last_error=error_message,
                    delay_seconds=retry_delay_seconds)
                logger.warning(
                    "[INITIALIZE] ↺ Application queued for retry after exception "
                    "application_id=%s retry=%s/%s delay=%ss",
                    application_id,
                    application.retry_count + 1, application.max_retries,
                    retry_delay_seconds)
                log_application_event(
                    db, application_id, EVT_WORKER_ERROR,
                    "Unhandled exception — retry %s/%s in %ss — %s" % (
                        application.retry_count + 1, application.max_retries,
                        retry_delay_seconds, error_message),
                    {"error": error_message, "retry_count": application.retry_count + 1,
                     "delay_s": retry_delay_seconds, "step": application.current_step})
            else:
                update_application_status(
                    db=db,
                    application=application,
                    status="failed",
                    current_step=application.current_step,
                    last_error=error_message)
                logger.error(
                    "[INITIALIZE] ✗ Application permanently failed after exception "
                    "application_id=%s retry_count=%s",
                    application_id, application.retry_count)
                log_application_event(
                    db, application_id, EVT_WORKER_ERROR,
                    "Permanently failed after unhandled exception — %s" % error_message,
                    {"error": error_message, "retry_count": application.retry_count,
                     "step": application.current_step})
    finally:
        # Always clear the log context so it doesn't leak into the next
        # application processed in the same worker loop iteration.
        clear_log_context()
        db.close()


def process_next_due_application(session: requests.Session):
    db = SessionLocal()
    try:
        application = get_next_processible_application(db)
        if not application:
            return False

        application_id = application.id
        logger.info(
            "[INITIALIZE] Picked application_id=%s tracking_id=%s status=%s step=%s",
            application_id, application.tracking_id,
            application.status, application.current_step or "eligibility_check")
    finally:
        db.close()

    process_application(application_id, session)
    return True


def reenqueue_due_retries(skip_application_id: int | None = None):
    db = SessionLocal()
    try:
        application = get_next_processible_application(db)
        if not application:
            return
        if skip_application_id and application.id == skip_application_id:
            logger.debug(
                "[INITIALIZE] Skipping re-enqueue for application_id=%s — already being processed",
                skip_application_id)
            return
        logger.info(
            "[INITIALIZE] Re-enqueuing due retry application_id=%s tracking_id=%s",
            application.id, application.tracking_id)
        enqueue_application_job(application.id, application.tracking_id)
    except Exception as exc:
        logger.exception("[INITIALIZE] Failed to re-enqueue due retry: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

def _log_startup_banner():
    logger.info("[INITIALIZE] ============================================")
    logger.info("[INITIALIZE]  FundingCircle Broker Worker — starting up")
    logger.info("[INITIALIZE] ============================================")
    logger.info("[INITIALIZE] Mode              : %s", "SQS" if settings.ENABLE_SQS else "DB poll")
    logger.info("[INITIALIZE] SQS URL           : %s", settings.SQS_QUEUE_URL or "not set")
    logger.info("[INITIALIZE] SQS visibility    : %ss", SQS_VISIBILITY_TIMEOUT_SECONDS)
    logger.info("[INITIALIZE] DB host           : %s", settings.POSTGRES_HOST)
    logger.info("[INITIALIZE] SF instance URL   : %s", settings.SALESFORCE_INSTANCE_URL or "NOT SET ⚠")
    logger.info("[INITIALIZE] SF client ID set  : %s", bool(settings.SALESFORCE_CLIENT_ID))
    logger.info("[INITIALIZE] OTP wait timeout  : %ss", settings.OTP_WAIT_SECONDS)
    logger.info("[INITIALIZE] Stuck threshold   : %s min", STUCK_PROCESSING_THRESHOLD_MINUTES)
    logger.info("[INITIALIZE] Heartbeat every   : %ss", HEARTBEAT_INTERVAL_SECONDS)
    logger.info("[INITIALIZE] ============================================")


# ---------------------------------------------------------------------------
# Poll loops
# ---------------------------------------------------------------------------

def run_local_poll_loop():
    session = requests.Session()
    logger.info("[INITIALIZE] Starting local DB poll loop (interval=%ss)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            _maybe_log_heartbeat()
            _maybe_check_for_stuck_applications()
            ensure_authenticated(session)
            processed = process_next_due_application(session)

            if not processed:
                logger.debug("[INITIALIZE] No work available, sleeping %ss", POLL_INTERVAL_SECONDS)
                time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as exc:
            logger.exception("[INITIALIZE] Worker-level error: %s", exc)

            try:
                db = SessionLocal()
                try:
                    invalidate_worker_auth(db, "funding_circle", error_message=str(exc))
                finally:
                    db.close()
            except Exception as db_exc:
                logger.exception("[INITIALIZE] Could not persist worker auth failure: %s", db_exc)

            logger.warning("[INITIALIZE] Clearing session cookies, sleeping 30s before retry")
            session.cookies.clear()
            time.sleep(30)


def run_sqs_poll_loop():
    import boto3

    if not settings.SQS_QUEUE_URL:
        raise ValueError("SQS_QUEUE_URL is not configured")

    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
    session = requests.Session()

    logger.info("[INITIALIZE] Starting SQS poll loop queue=%s", settings.SQS_QUEUE_URL)

    while True:
        try:
            _maybe_log_heartbeat()
            _maybe_check_for_stuck_applications()

            response = sqs.receive_message(
                QueueUrl=settings.SQS_QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=SQS_VISIBILITY_TIMEOUT_SECONDS)

            messages = response.get("Messages", [])
            if not messages:
                logger.debug("[INITIALIZE] No SQS messages received")
                continue

            for message in messages:
                receipt_handle = message["ReceiptHandle"]
                body = json.loads(message["Body"])
                application_id = body["application_id"]
                tracking_id = body.get("tracking_id", "unknown")

                logger.info(
                    "[INITIALIZE] Received SQS message application_id=%s tracking_id=%s",
                    application_id, tracking_id)

                # Re-enqueue due retries but skip this application to avoid double processing
                reenqueue_due_retries(skip_application_id=application_id)

                try:
                    ensure_authenticated(session)
                    process_application(application_id, session)
                    sqs.delete_message(
                        QueueUrl=settings.SQS_QUEUE_URL,
                        ReceiptHandle=receipt_handle)
                    logger.info(
                        "[INITIALIZE] SQS message deleted application_id=%s tracking_id=%s",
                        application_id, tracking_id)
                except Exception as exc:
                    logger.exception(
                        "[INITIALIZE] Failed to process SQS message "
                        "application_id=%s tracking_id=%s error=%s",
                        application_id, tracking_id, exc)
                    if "429" in str(exc):
                        logger.warning("[INITIALIZE] Rate limited by FC — sleeping 60s")
                        time.sleep(60)
                    elif "401" in str(exc) and "mfa_entry" in str(exc):
                        logger.warning("[INITIALIZE] OTP rejected by FC (stale OTP) — sleeping 30s")
                        time.sleep(30)

        except Exception as exc:
            logger.exception("[INITIALIZE] SQS poll loop error: %s", exc)
            logger.warning("[INITIALIZE] Sleeping 30s before retrying SQS loop")
            time.sleep(30)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    setup_logging()
    _log_startup_banner()
    _validate_config()
    _recover_stuck_applications()

    if settings.ENABLE_SQS:
        run_sqs_poll_loop()
    else:
        run_local_poll_loop()


if __name__ == "__main__":
    main()