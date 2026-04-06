"""
Application log context using Python contextvars.

Sets tracking_id, salesforce_record_id, fc_application_id, and the active
DB session once at the start of process_application(). These propagate
automatically to every log line and every function called within that
context — no need to pass them through every function signature.

Usage:
    from rise.logging_context import set_log_context, clear_log_context

    set_log_context(
        tracking_id=application.tracking_id,
        salesforce_record_id=application.salesforce_record_id)
    try:
        ...
    finally:
        clear_log_context()
"""
import logging
from typing import Any
from contextvars import ContextVar

_tracking_id: ContextVar[str] = ContextVar("tracking_id", default="")
_salesforce_record_id: ContextVar[str] = ContextVar("salesforce_record_id", default="")
_fc_application_id: ContextVar[str] = ContextVar("fc_application_id", default="")

# DB context — set by run_persisted_step so that step functions which don't
# receive db as a parameter can still write application events.
_db: ContextVar[Any] = ContextVar("db", default=None)
_db_application_id: ContextVar[int | None] = ContextVar("db_application_id", default=None)


def set_log_context(
    tracking_id: str = "",
    salesforce_record_id: str = "",
    fc_application_id: str = ""
) -> None:
    _tracking_id.set(tracking_id or "")
    _salesforce_record_id.set(salesforce_record_id or "")
    _fc_application_id.set(fc_application_id or "")


def update_fc_application_id(fc_application_id: str) -> None:
    """
    Called once eligibility_check returns the FC application_id so that all
    subsequent log lines automatically carry it.
    """
    _fc_application_id.set(fc_application_id or "")


def set_db_context(db: Any, db_application_id: int | None) -> None:
    """
    Called by run_persisted_step so that step functions (e.g. step_submit_bank_statements)
    can write application events without needing db passed through every call.
    """
    _db.set(db)
    _db_application_id.set(db_application_id)


def get_db_context() -> tuple[Any, int | None]:
    """Returns the active (db, db_application_id) tuple from context."""
    return _db.get(), _db_application_id.get()


def clear_log_context() -> None:
    _tracking_id.set("")
    _salesforce_record_id.set("")
    _fc_application_id.set("")
    _db.set(None)
    _db_application_id.set(None)


class AppContextFilter(logging.Filter):
    """
    Injects app_context into every log record.

    When an application is being processed the format becomes:
        [tracking=abc12345 | sf=a0B8d000XYZ | fc=def45678] message here

    When no context is active (startup logs, API request logs) the field is
    empty so nothing extra appears in the line.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        tid = _tracking_id.get()
        sfid = _salesforce_record_id.get()
        fcid = _fc_application_id.get()

        parts = []
        if tid:
            parts.append("tracking=%s" % tid[:8])
        if sfid:
            parts.append("sf=%s" % sfid)
        if fcid:
            parts.append("fc=%s" % fcid[:8])

        record.app_context = "[%s] " % " | ".join(parts) if parts else ""
        return True