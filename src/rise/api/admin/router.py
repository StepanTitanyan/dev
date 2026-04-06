import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from rise.db.session import get_db
from rise.db import repositories as repo
from rise.db.models import WorkerSession
from rise.api.admin.cloudwatch import fetch_log_events, list_log_groups

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["dt"] = lambda value: (
    "—" if value is None
    else value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime)
    else str(value)
)


def _company_name(app) -> str:
    try:
        company = app.raw_input_json.get("company", {})
        return (
            company.get("company_name")
            or company.get("company_search_term")
            or "—"
        )
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    metrics = repo.get_application_metrics(db)
    recent_apps, _ = repo.list_applications(db, limit=10)
    sessions = db.query(WorkerSession).order_by(WorkerSession.worker_type).all()
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "active": "dashboard",
        "metrics": metrics,
        "recent_apps": recent_apps,
        "sessions": sessions,
        "company_name": _company_name,
    })


# ---------------------------------------------------------------------------
# Applications list
# ---------------------------------------------------------------------------

PAGE_SIZE = 50

@router.get("/applications", response_class=HTMLResponse)
def applications_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str = Query(""),
    status: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
):
    offset = (page - 1) * PAGE_SIZE

    date_from_dt = None
    date_to_dt = None
    try:
        if date_from:
            date_from_dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if date_to:
            date_to_dt = (
                datetime.strptime(date_to, "%Y-%m-%d")
                .replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            )
    except ValueError:
        pass

    apps, total = repo.list_applications_admin(
        db,
        search=search or None,
        status=status or None,
        date_from=date_from_dt,
        date_to=date_to_dt,
        limit=PAGE_SIZE,
        offset=offset,
    )
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse(request, "admin/applications.html", {
        "active": "applications",
        "apps": apps,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "company_name": _company_name,
        "statuses": ["queued", "processing", "retrying", "completed", "partially_completed", "failed"],
    })


# ---------------------------------------------------------------------------
# Application detail
# ---------------------------------------------------------------------------

@router.get("/applications/{application_id}", response_class=HTMLResponse)
def application_detail(
    request: Request,
    application_id: int,
    db: Session = Depends(get_db),
):
    app = repo.get_application_by_id(db, application_id)
    if not app:
        return HTMLResponse("<h3>Application not found</h3>", status_code=404)

    steps = repo.list_application_steps(db, application_id)
    events = repo.list_application_events(db, application_id)

    raw_json = json.dumps(app.raw_input_json, indent=2) if app.raw_input_json else "{}"
    working_json = json.dumps(app.working_payload_json, indent=2) if app.working_payload_json else "{}"

    return templates.TemplateResponse(request, "admin/application_detail.html", {
        "active": "applications",
        "app": app,
        "steps": steps,
        "events": events,
        "raw_json": raw_json,
        "working_json": working_json,
        "company_name": _company_name(app),
    })


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

@router.get("/workers", response_class=HTMLResponse)
def workers(request: Request, db: Session = Depends(get_db)):
    sessions = db.query(WorkerSession).order_by(WorkerSession.worker_type).all()
    from rise.db.models import OtpMessage
    recent_otps = {}
    for s in sessions:
        recent_otps[s.worker_type] = (
            db.query(OtpMessage)
            .filter(OtpMessage.service == s.worker_type)
            .order_by(OtpMessage.received_at.desc())
            .limit(5)
            .all()
        )
    return templates.TemplateResponse(request, "admin/workers.html", {
        "active": "workers",
        "sessions": sessions,
        "recent_otps": recent_otps,
    })


# ---------------------------------------------------------------------------
# CloudWatch Logs
# ---------------------------------------------------------------------------

DEFAULT_LOG_GROUP = "/ecs/rise-api"

@router.get("/logs", response_class=HTMLResponse)
def logs(
    request: Request,
    log_group: str = Query(DEFAULT_LOG_GROUP),
    filter_pattern: str = Query(""),
    hours: int = Query(1, ge=1, le=168),
):
    log_groups = list_log_groups("/ecs/rise")
    if not log_groups:
        log_groups = [DEFAULT_LOG_GROUP]

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(hours=hours)

    events, error = fetch_log_events(
        log_group=log_group,
        filter_pattern=filter_pattern,
        start_dt=start_dt,
        end_dt=end_dt,
    )

    return templates.TemplateResponse(request, "admin/logs.html", {
        "active": "logs",
        "log_groups": log_groups,
        "log_group": log_group,
        "filter_pattern": filter_pattern,
        "hours": hours,
        "events": events,
        "error": error,
    })
