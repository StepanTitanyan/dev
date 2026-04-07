import logging
from datetime import datetime

from rise.config.config import settings

logger = logging.getLogger(__name__)


KNOWN_LOG_GROUPS = [
    "/ecs/rise-api",
    "/ecs/rise-worker",
    "/ecs/rise-worker-prod"]

def list_log_groups(prefix: str = "/ecs/rise") -> list[str]:
    try:
        import boto3
        client = boto3.client("logs", region_name=settings.AWS_REGION)
        resp = client.describe_log_groups(logGroupNamePrefix=prefix, limit=20)
        groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
        # Merge with known groups so they always appear even if CW returns nothing
        for g in KNOWN_LOG_GROUPS:
            if g not in groups:
                groups.append(g)
        return sorted(groups)
    except Exception:
        logger.exception("Failed to list CloudWatch log groups")
        return KNOWN_LOG_GROUPS


def fetch_log_events(
    log_group: str,
    filter_pattern: str = "",
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
    limit: int = 700,
) -> tuple[list[dict], str | None]:
    """Returns (events, error_message). events is a list of dicts with timestamp/message."""
    try:
        import boto3
        client = boto3.client("logs", region_name=settings.AWS_REGION)
        kwargs: dict = {
            "logGroupName": log_group,
            "limit": limit,
            "interleaved": True,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern
        if start_dt:
            kwargs["startTime"] = int(start_dt.timestamp() * 1000)
        if end_dt:
            kwargs["endTime"] = int(end_dt.timestamp() * 1000)

        resp = client.filter_log_events(**kwargs)
        events = []
        for e in resp.get("events", []):
            events.append({
                "timestamp": datetime.fromtimestamp(e["timestamp"] / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                "message": e.get("message", "").strip(),
                "stream": e.get("logStreamName", ""),
            })
        return events, None
    except Exception as exc:
        logger.exception("Failed to fetch CloudWatch logs for group=%s", log_group)
        return [], str(exc)
