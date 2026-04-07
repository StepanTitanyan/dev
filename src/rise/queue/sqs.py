import json
import logging

from rise.config.config import settings

logger = logging.getLogger(__name__)


def enqueue_application_job(application_id: int, tracking_id: str):
    resolved_url = settings.SQS_QUEUE_URL
    if not settings.ENABLE_SQS or not resolved_url:
        logger.info(
            "[QUEUE:DISABLED] Would enqueue application_id=%s, tracking_id=%s, enable_sqs=%s, queue_url_present=%s",
            application_id,
            tracking_id,
            settings.ENABLE_SQS,
            bool(settings.SQS_QUEUE_URL))
        return {
            "queued": False,
            "mode": "disabled",
            "application_id": application_id,
            "tracking_id": tracking_id}

    logger.info(
        "[QUEUE:START] Enqueuing application_id=%s, tracking_id=%s, region=%s",
        application_id,
        tracking_id,
        settings.AWS_REGION)

    import boto3
    from botocore.config import Config as BotocoreConfig

    try:
        sqs = boto3.client("sqs", region_name=settings.AWS_REGION,
                           config=BotocoreConfig(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}))
        response = sqs.send_message(QueueUrl=settings.SQS_QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "application_id": application_id,
                    "tracking_id": tracking_id,
                }))
        logger.info(
            "[QUEUE:SUCCESS] application_id=%s, tracking_id=%s, message_id=%s",
            application_id,
            tracking_id,
            response.get("MessageId"))
        return {
            "queued": True,
            "message_id": response.get("MessageId"),
            "application_id": application_id,
            "tracking_id": tracking_id}
    except Exception:
        logger.exception(
            "[QUEUE:ERROR] Failed to enqueue application_id=%s, tracking_id=%s",
            application_id,
            tracking_id)
        raise