import uuid
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query

from rise.db.models import Application
from rise.db.session import get_db
from rise.db.repositories import (
    create_application,
    get_application_by_salesforce_record_id,
    get_application_by_tracking_id,
    list_application_steps,
    list_application_events,
    get_worker_session_status,
    list_applications,
    get_application_metrics,
    update_application_status)
from rise.queue.sqs import enqueue_application_job
from rise.api.validators.registry import get_validator
from rise.api.validators.funding_circle import FundingCirclePayload
from rise.api.schemas.responses import (
    ApplicationCreateResponse,
    ApplicationStatusResponse,
    ApplicationListResponse,
    ApplicationStepsResponse,
    ApplicationEventsResponse,
    WorkerSessionResponse)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Applications"])


def _to_status_response(application: Application) -> dict:
    return {
        "tracking_id": application.tracking_id,
        "salesforce_record_id": application.salesforce_record_id,
        "external_id": application.external_id,
        "worker_type": application.worker_type,
        "status": application.status,
        "current_step": application.current_step,
        "retry_count": application.retry_count,
        "last_error": application.last_error,
        "created_at": application.created_at.isoformat() if application.created_at else None,
        "updated_at": application.updated_at.isoformat() if application.updated_at else None,
        "completed_at": application.finished_at.isoformat() if application.finished_at else None}


def _build_system_block(tracking_id: str) -> dict:
    return {
        "application_id": None,
        "processing_tracking_id": tracking_id,
        "processing_files_dir": None,
        "bank_statements_action_type": None,
        "uploaded_documents": [],
        "uploaded_bank_statement_documents": [],
        "potential_executive_business_owners": [],
        "majority_executive_business_owners": []}


def _enqueue(application: Application, mock: bool):
    if mock:
        logger.info("[SQS:ENQUEUE_SKIPPED] mock=true tracking_id=%s", application.tracking_id)
        return
    try:
        enqueue_application_job(
            application_id=application.id,
            tracking_id=application.tracking_id)
        logger.info("[SQS:ENQUEUE_SUCCESS] tracking_id=%s", application.tracking_id)
    except Exception:
        logger.exception(
            "[SQS:ENQUEUE_ERROR] tracking_id=%s — saved to DB, will rely on DB polling",
            application.tracking_id)


@router.get("/health", summary="Health check", tags=["System"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@router.post(
    "/applications/{company}",
    response_model=ApplicationCreateResponse,
    summary="Submit a new application",
    description="Accepts a Salesforce-shaped payload for the given lender (e.g. `funding-circle`). "
                "Returns a `tracking_id` to poll for status. Idempotent — re-submitting the same "
                "`salesforce_record_id` returns the existing application.")
def create_application_endpoint(
    company: str,
    raw: FundingCirclePayload,
    db: Session = Depends(get_db)
):
    validator = get_validator(company)
    payload = raw.model_dump()

    existing = get_application_by_salesforce_record_id(db, payload.get("salesforce_record_id", ""))
    if existing:
        logger.info(
            "[APPLICATION:EXISTS] salesforce_record_id=%s tracking_id=%s status=%s",
            payload.get("salesforce_record_id"), existing.tracking_id, existing.status)
        return ApplicationCreateResponse(tracking_id=existing.tracking_id, status=existing.status)

    tracking_id = str(uuid.uuid4())
    payload["system"] = _build_system_block(tracking_id)

    application = create_application(
        db=db,
        salesforce_payload=payload,
        worker_type=validator.worker_type,
        initial_status="queued",
        initial_step="eligibility_check",
        tracking_id=tracking_id)

    logger.info(
        "[APPLICATION:CREATED] id=%s tracking_id=%s worker_type=%s",
        application.id, application.tracking_id, application.worker_type)

    _enqueue(application, mock=payload.get("mock", False))
    return ApplicationCreateResponse(tracking_id=application.tracking_id, status=application.status)


@router.get(
    "/applications",
    response_model=ApplicationListResponse,
    summary="List applications",
    description="Returns a paginated list of applications. Filter by `status` or `worker_type`.")
def list_applications_endpoint(
    status: str | None = Query(default=None, description="Filter by status: queued | processing | completed | failed | retrying"),
    worker_type: str | None = Query(default=None, description="Filter by lender worker, e.g. 'funding_circle'"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    applications, total = list_applications(
        db=db, status=status, worker_type=worker_type, limit=limit, offset=offset)

    return {
        "applications": [
            {
                "id": a.id,
                "tracking_id": a.tracking_id,
                "salesforce_record_id": a.salesforce_record_id,
                "external_id": a.external_id,
                "worker_type": a.worker_type,
                "status": a.status,
                "current_step": a.current_step,
                "company_name": (a.working_payload_json or {}).get("company", {}).get("company_name"),
                "last_error": a.last_error,
                "retry_count": a.retry_count,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None}
            for a in applications],
        "total": total,
        "limit": limit,
        "offset": offset}


@router.get(
    "/applications/{tracking_id}",
    response_model=ApplicationStatusResponse,
    summary="Get application status")
def get_application_endpoint(tracking_id: str, db: Session = Depends(get_db)):
    application = get_application_by_tracking_id(db, tracking_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return _to_status_response(application)


@router.get("/applications/{tracking_id}/steps", response_model=ApplicationStepsResponse, summary="Get workflow steps")
def get_application_steps_endpoint(tracking_id: str, db: Session = Depends(get_db)):
    application = get_application_by_tracking_id(db, tracking_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    steps = list_application_steps(db=db, application_id=application.id)
    return {
        "tracking_id": tracking_id,
        "steps": [
            {
                "id": s.id,
                "step_name": s.step_name,
                "step_order": s.step_order,
                "status": s.status,
                "request_json": s.request_json,
                "response_json": s.response_json,
                "error_message": s.error_message,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None}
            for s in steps]}


@router.get("/applications/{tracking_id}/events", response_model=ApplicationEventsResponse, summary="Get application event log")
def get_application_events_endpoint(tracking_id: str, db: Session = Depends(get_db)):
    application = get_application_by_tracking_id(db, tracking_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    events = list_application_events(db=db, application_id=application.id)
    return {
        "tracking_id": tracking_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "message": e.message,
                "data": e.data,
                "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in events]}


@router.post(
    "/applications/{tracking_id}/retry",
    response_model=ApplicationStatusResponse,
    summary="Manually retry a failed application",
    description="Re-queues an application that is in `failed` or `retrying` status.")
def retry_application_endpoint(tracking_id: str, db: Session = Depends(get_db)):
    application = get_application_by_tracking_id(db, tracking_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status not in {"failed", "retrying"}:
        raise HTTPException(status_code=400, detail="Application is not in a retryable state")

    updated = update_application_status(
        db=db,
        application=application,
        status="queued",
        current_step=application.current_step or "eligibility_check",
        last_error=None)

    try:
        enqueue_application_job(updated.id, updated.tracking_id)
        logger.info("[RETRY:ENQUEUED] tracking_id=%s", tracking_id)
    except Exception:
        logger.exception("[RETRY:ENQUEUE_ERROR] tracking_id=%s", tracking_id)

    return _to_status_response(updated)


@router.get("/metrics", summary="Today's application metrics", tags=["System"])
def get_metrics_endpoint(db: Session = Depends(get_db)):
    return get_application_metrics(db)


@router.get("/worker/status", response_model=WorkerSessionResponse, summary="Worker authentication status", tags=["System"])
def get_worker_status_endpoint(
    worker_type: str = Query(default="funding_circle", description="Which lender worker to check"),
    db: Session = Depends(get_db)
):
    return get_worker_session_status(db, worker_type)


# Aliases — kept for backward compatibility, hidden from docs

@router.get("/applications/funding-circle", include_in_schema=False)
def list_applications_alias(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    return list_applications_endpoint(
        status=status, worker_type="funding_circle", limit=limit, offset=offset, db=db)


@router.get("/applications/{tracking_id}/funding-circle", response_model=ApplicationStatusResponse, include_in_schema=False)
def get_application_alias(tracking_id: str, db: Session = Depends(get_db)):
    return get_application_endpoint(tracking_id=tracking_id, db=db)


@router.get("/applications/{tracking_id}/steps/funding-circle", include_in_schema=False)
def get_steps_alias(tracking_id: str, db: Session = Depends(get_db)):
    return get_application_steps_endpoint(tracking_id=tracking_id, db=db)


@router.get("/applications/{tracking_id}/events/funding-circle", include_in_schema=False)
def get_events_alias(tracking_id: str, db: Session = Depends(get_db)):
    return get_application_events_endpoint(tracking_id=tracking_id, db=db)


@router.post("/applications/{tracking_id}/retry/funding-circle", response_model=ApplicationStatusResponse, include_in_schema=False)
def retry_alias(tracking_id: str, db: Session = Depends(get_db)):
    return retry_application_endpoint(tracking_id=tracking_id, db=db)


@router.get("/metrics/funding-circle", include_in_schema=False)
def get_metrics_alias(db: Session = Depends(get_db)):
    return get_metrics_endpoint(db=db)


@router.get("/worker/status/funding-circle", include_in_schema=False)
def get_worker_status_alias(db: Session = Depends(get_db)):
    return get_worker_status_endpoint(worker_type="funding_circle", db=db)
