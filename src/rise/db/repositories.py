import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from rise.db.models import Application, ApplicationStep, ApplicationEvent, WorkerSession, OtpMessage


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

def create_application(
    db: Session,
    salesforce_payload: dict,
    worker_type: str,
    initial_status: str = "queued",
    initial_step: str = "eligibility_check",
    tracking_id: str | None = None
) -> Application:
    application = Application(
        tracking_id=tracking_id or str(uuid.uuid4()),
        salesforce_record_id=salesforce_payload.get("salesforce_record_id"),
        worker_type=worker_type,
        status=initial_status,
        current_step=initial_step,
        raw_input_json=salesforce_payload,
        working_payload_json=salesforce_payload,
        next_retry_at=None)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_application_by_id(db: Session, application_id: int) -> Application | None:
    return db.query(Application).filter(Application.id == application_id).first()


def get_application_by_tracking_id(db: Session, tracking_id: str) -> Application | None:
    return db.query(Application).filter(Application.tracking_id == tracking_id).first()


def get_application_by_salesforce_record_id(db: Session, salesforce_record_id: str) -> Application | None:
    return (
        db.query(Application)
        .filter(Application.salesforce_record_id == salesforce_record_id)
        .first())


def list_applications(
    db: Session,
    status: str | None = None,
    worker_type: str | None = None,
    limit: int = 20,
    offset: int = 0
) -> tuple[list[Application], int]:
    query = db.query(Application)
    if status:
        query = query.filter(Application.status == status)
    if worker_type:
        query = query.filter(Application.worker_type == worker_type)
    total = query.count()
    applications = query.order_by(Application.created_at.desc()).offset(offset).limit(limit).all()
    return applications, total


def get_next_processible_application(db: Session) -> Application | None:
    now = utcnow()
    return (
        db.query(Application)
        .filter(
            or_(
                Application.status == "queued",
                and_(
                    Application.status == "retrying",
                    Application.next_retry_at.isnot(None),
                    Application.next_retry_at <= now)))
        .order_by(Application.created_at.asc())
        .first())


def update_application_status(
    db: Session,
    application: Application,
    status: str,
    current_step: str | None = None,
    last_error: str | None = None
) -> Application:
    application.status = status
    application.current_step = current_step
    application.last_error = last_error
    application.updated_at = utcnow()
    if status == "processing" and application.started_at is None:
        application.started_at = utcnow()
    if status in {"completed", "failed"}:
        application.finished_at = utcnow()
        application.next_retry_at = None
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_application_current_step(
    db: Session,
    application: Application,
    current_step: str | None,
    last_error: str | None = None
) -> Application:
    application.current_step = current_step
    application.last_error = last_error
    application.updated_at = utcnow()
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def update_working_payload(db: Session, application: Application, working_payload: dict) -> Application:
    application.working_payload_json = working_payload
    application.updated_at = utcnow()
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def set_external_id(db: Session, application: Application, external_id: str) -> Application:
    application.external_id = external_id
    application.updated_at = utcnow()
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def mark_application_for_retry(
    db: Session,
    application: Application,
    current_step: str | None,
    last_error: str | None,
    delay_seconds: int
) -> Application:
    application.retry_count += 1
    application.status = "retrying"
    application.current_step = current_step
    application.last_error = last_error
    application.next_retry_at = utcnow() + timedelta(seconds=delay_seconds)
    application.updated_at = utcnow()
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def reset_stuck_processing_applications(db: Session, stuck_after_minutes: int = 30) -> int:
    cutoff = utcnow() - timedelta(minutes=stuck_after_minutes)
    stuck = (
        db.query(Application)
        .filter(Application.status == "processing", Application.updated_at < cutoff)
        .all())
    count = 0
    for application in stuck:
        application.status = "queued"
        application.last_error = (
            "Reset from stuck 'processing' state after %s minutes — worker likely crashed"
            % stuck_after_minutes)
        application.updated_at = utcnow()
        db.add(application)
        count += 1
    if count > 0:
        db.commit()
    return count


def list_applications_admin(
    db: Session,
    search: str | None = None,
    status: str | None = None,
    worker_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Application], int]:
    from sqlalchemy import cast
    from sqlalchemy.types import Text

    query = db.query(Application)
    if status:
        query = query.filter(Application.status == status)
    if worker_type:
        query = query.filter(Application.worker_type == worker_type)
    if date_from:
        query = query.filter(Application.created_at >= date_from)
    if date_to:
        query = query.filter(Application.created_at <= date_to)
    if search:
        s = f"%{search}%"
        query = query.filter(
            or_(
                Application.tracking_id.ilike(s),
                Application.salesforce_record_id.ilike(s),
                cast(Application.raw_input_json["company"]["company_name"], Text).ilike(s),
                cast(Application.raw_input_json["company"]["company_search_term"], Text).ilike(s),
            )
        )
    total = query.count()
    apps = query.order_by(Application.created_at.desc()).offset(offset).limit(limit).all()
    return apps, total


def get_application_metrics(db: Session) -> dict:
    from sqlalchemy import func
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(Application.status, func.count(Application.id))
        .filter(Application.created_at >= today_start)
        .group_by(Application.status)
        .all())
    counts = {status: count for status, count in rows}
    return {
        "submitted_today": sum(counts.values()),
        "processing": counts.get("processing", 0),
        "completed": counts.get("completed", 0) + counts.get("partially_completed", 0),
        "failed": counts.get("failed", 0),
        "retrying": counts.get("retrying", 0),
        "queued": counts.get("queued", 0)}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def list_application_steps(db: Session, application_id: int) -> list[ApplicationStep]:
    return (
        db.query(ApplicationStep)
        .filter(ApplicationStep.application_id == application_id)
        .order_by(ApplicationStep.step_order.asc())
        .all())


def get_application_step(db: Session, application_id: int, step_name: str) -> ApplicationStep | None:
    return (
        db.query(ApplicationStep)
        .filter(
            ApplicationStep.application_id == application_id,
            ApplicationStep.step_name == step_name)
        .first())


def create_step(
    db: Session,
    application_id: int,
    step_name: str,
    step_order: int,
    status: str = "started",
    request_json: dict | None = None
) -> ApplicationStep:
    step = get_application_step(db, application_id, step_name)
    if step:
        step.step_order = step_order
        step.status = status
        step.request_json = request_json
        step.response_json = None
        step.error_message = None
        step.started_at = utcnow()
        step.finished_at = None
    else:
        step = ApplicationStep(
            application_id=application_id,
            step_name=step_name,
            step_order=step_order,
            status=status,
            request_json=request_json,
            started_at=utcnow())
        db.add(step)
    db.commit()
    db.refresh(step)
    return step


def complete_step(db: Session, step: ApplicationStep, response_json: dict | None = None) -> ApplicationStep:
    step.status = "succeeded"
    step.response_json = response_json
    step.error_message = None
    step.finished_at = utcnow()
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def fail_step(db: Session, step: ApplicationStep, error_message: str, response_json: dict | None = None) -> ApplicationStep:
    step.status = "failed"
    step.error_message = error_message
    step.response_json = response_json
    step.finished_at = utcnow()
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

EVT_PROCESSING_STARTED   = "processing_started"
EVT_STEP_STARTED         = "step_started"
EVT_STEP_COMPLETED       = "step_completed"
EVT_STEP_FAILED          = "step_failed"
EVT_SF_DOWNLOAD_STARTED  = "sf_download_started"
EVT_SF_DOWNLOAD_DONE     = "sf_download_completed"
EVT_SF_DOWNLOAD_FAILED   = "sf_download_failed"
EVT_FC_REJECTED          = "fc_rejected"
EVT_FC_VALIDATION_ERROR  = "fc_validation_error"
EVT_RETRY_SCHEDULED      = "retry_scheduled"
EVT_COMPLETED            = "completed"
EVT_PARTIALLY_COMPLETED  = "partially_completed"
EVT_FAILED               = "failed"
EVT_WORKER_ERROR         = "worker_error"


def log_application_event(
    db: Session,
    application_id: int,
    event_type: str,
    message: str | None = None,
    data: dict | None = None
):
    import logging
    _logger = logging.getLogger(__name__)
    try:
        event = ApplicationEvent(
            application_id=application_id,
            event_type=event_type,
            message=message,
            data=data)
        db.add(event)
        db.commit()
        return event
    except Exception as exc:
        db.rollback()
        _logger.warning(
            "[DB] Failed to log event event_type=%s application_id=%s error=%s",
            event_type, application_id, exc)
        return None


def list_application_events(db: Session, application_id: int) -> list[ApplicationEvent]:
    return (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == application_id)
        .order_by(ApplicationEvent.created_at.asc())
        .all())


# ---------------------------------------------------------------------------
# Worker sessions
# ---------------------------------------------------------------------------

def get_or_create_worker_session(db: Session, worker_type: str) -> WorkerSession:
    session = db.query(WorkerSession).filter(WorkerSession.worker_type == worker_type).first()
    if session:
        return session
    session = WorkerSession(worker_type=worker_type, status="logged_out", is_authenticated=False)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_worker_session_status(db: Session, worker_type: str) -> dict:
    session = get_or_create_worker_session(db, worker_type)
    return {
        "worker_type": session.worker_type,
        "status": session.status,
        "is_authenticated": session.is_authenticated,
        "waiting_for_otp_since": session.waiting_for_otp_since.isoformat() if session.waiting_for_otp_since else None,
        "otp_received_at": session.otp_received_at.isoformat() if session.otp_received_at else None,
        "last_login_at": session.last_login_at.isoformat() if session.last_login_at else None,
        "last_error": session.last_error,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None}


def set_worker_waiting_for_otp(db: Session, worker_type: str, auth_session_token: str | None = None) -> WorkerSession:
    session = get_or_create_worker_session(db, worker_type)
    session.status = "waiting_for_otp"
    session.session_data = {"auth_session_token": auth_session_token} if auth_session_token else {}
    session.waiting_for_otp_since = utcnow()
    session.otp_received_at = None
    session.last_error = None
    session.is_authenticated = False
    session.updated_at = utcnow()
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def mark_worker_authenticated(db: Session, worker_type: str) -> WorkerSession:
    session = get_or_create_worker_session(db, worker_type)
    session.status = "authenticated"
    session.waiting_for_otp_since = None
    session.last_login_at = utcnow()
    session.last_error = None
    session.is_authenticated = True
    session.updated_at = utcnow()
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def invalidate_worker_auth(db: Session, worker_type: str, error_message: str | None = None) -> WorkerSession:
    session = get_or_create_worker_session(db, worker_type)
    session.status = "logged_out"
    session.session_data = None
    session.waiting_for_otp_since = None
    session.last_error = error_message
    session.is_authenticated = False
    session.updated_at = utcnow()
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------

def store_incoming_otp_message(
    db: Session,
    service: str,
    phone_from: str | None,
    phone_to: str | None,
    message_body: str | None,
    otp_code: str | None
) -> OtpMessage:
    otp_message = OtpMessage(
        service=service,
        phone_from=phone_from,
        phone_to=phone_to,
        message_body=message_body,
        otp_code=otp_code,
        status="received")
    db.add(otp_message)
    db.commit()
    db.refresh(otp_message)
    return otp_message


def get_latest_worker_otp(db: Session, worker_type: str) -> str | None:
    msg = (
        db.query(OtpMessage)
        .filter(OtpMessage.service == worker_type, OtpMessage.status == "received")
        .order_by(OtpMessage.received_at.desc())
        .first())
    return msg.otp_code if msg else None


def consume_latest_worker_otp(db: Session, worker_type: str) -> str | None:
    msg = (
        db.query(OtpMessage)
        .filter(OtpMessage.service == worker_type, OtpMessage.status == "received")
        .order_by(OtpMessage.received_at.desc())
        .first())
    if not msg:
        return None
    msg.status = "consumed"
    msg.consumed_at = utcnow()
    db.add(msg)

    session = get_or_create_worker_session(db, worker_type)
    session.otp_received_at = utcnow()
    db.add(session)

    db.commit()
    return msg.otp_code
