import logging
import re
from fastapi import APIRouter, Form, Query
from rise.db.repositories import store_incoming_otp_message, get_worker_session_status
from rise.db.session import SessionLocal

router = APIRouter(tags=["OTP / Webhooks"])
logger = logging.getLogger(__name__)


def extract_otp_from_text(message_text: str) -> str | None:
    match = re.search(r"\b(\d{4,8})\b", message_text or "")
    return match.group(1) if match else None


@router.post("/sms", summary="Twilio inbound SMS webhook", description="Receives an inbound SMS from Twilio, extracts the OTP code, and stores it for the worker to consume.")
def sms_webhook(
    From: str = Form(default=None),
    To: str = Form(default=None),
    Body: str = Form(default=None),
    service: str = Form(default="funding_circle")
):
    otp_code = extract_otp_from_text(Body)
    db = SessionLocal()
    try:
        store_incoming_otp_message(
            db=db,
            service=service,
            phone_from=From,
            phone_to=To,
            message_body=Body,
            otp_code=otp_code)
    finally:
        db.close()
    logger.info("[OTP:RECEIVED] service=%s otp_received=%s", service, bool(otp_code))
    return {"ok": True, "otp_received": bool(otp_code)}


@router.get("/latest-sms", summary="Get latest OTP status for a service")
def get_latest_sms(service: str = Query(default="funding_circle", description="Which service to check, e.g. 'funding_circle'")):
    db = SessionLocal()
    try:
        return get_worker_session_status(db, service)
    finally:
        db.close()
