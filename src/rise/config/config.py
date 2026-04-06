import os
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")

def setup_logging():
    if logging.getLogger().handlers:
        return

    from rise.logging_context import AppContextFilter

    # The filter sets app_context on every record. We also install it on the
    # root logger (not just the handler) so it runs for all handlers.
    # The format string uses %(app_context)s — if for any reason a log record
    # reaches the formatter without the filter having run first (e.g. a third-
    # party library with its own handler), the format would crash with KeyError.
    # We guard against this with a custom formatter that supplies a safe default.
    class SafeFormatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, "app_context"):
                record.app_context = ""
            return super().format(record)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeFormatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(app_context)s%(message)s"))

    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    root.addHandler(handler)
    root.addFilter(AppContextFilter())

class Settings:
    def __init__(self) -> None:
        self.APP_USERNAME: str | None = os.getenv("APP_USERNAME")
        self.APP_PASSWORD: str | None = os.getenv("APP_PASSWORD")

        self.OTP_WAIT_SECONDS: int = int(os.getenv("OTP_WAIT_SECONDS", "120"))

        self.APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
        self.APP_PORT: int = int(os.getenv("APP_PORT", "8000"))

        self.POSTGRES_DB: str = os.getenv("POSTGRES_DB", "rise")
        self.POSTGRES_USER: str = os.getenv("POSTGRES_USER", "fc_user")
        self.POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "fc_password")
        self.POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
        self.POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))

        self.DB_SECRET_JSON: str = os.getenv("DB_SECRET_JSON", "")
        self.AWS_REGION: str = os.getenv("AWS_REGION", "eu-west-2")
        self.SQS_QUEUE_URL: str | None = os.getenv("SQS_QUEUE_URL")
        self.ENABLE_SQS: bool = os.getenv("ENABLE_SQS", "false").lower() == "true"

        # API auth token (HMAC-based, date-rotated)
        self.API_BASE_TOKEN: str = os.getenv("API_BASE_TOKEN", "")

        # Admin UI Basic Auth
        self.ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "")
        self.ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

        # Salesforce REST API — used by the worker to download ContentVersion files
        self.SALESFORCE_INSTANCE_URL: str = os.getenv("SALESFORCE_INSTANCE_URL", "")
        self.SALESFORCE_CLIENT_ID: str = os.getenv("SALESFORCE_CLIENT_ID", "")
        self.SALESFORCE_CLIENT_SECRET: str = os.getenv("SALESFORCE_CLIENT_SECRET", "")
        self.SALESFORCE_API_VERSION: str = os.getenv("SALESFORCE_API_VERSION", "v60.0")

        # FundingCircle API base URLs
        self.FC_AUTH_BASE_URL: str = os.getenv("FC_AUTH_BASE_URL", "https://fc-auth-api.fundingcircle.com")
        self.FC_API_BASE_URL: str = os.getenv("FC_API_BASE_URL", "https://borrower-api.fundingcircle.com")
        self.FC_WEB_BASE_URL: str = os.getenv("FC_WEB_BASE_URL", "https://www.fundingcircle.com")

        self._apply_secret_overrides()

    def _apply_secret_overrides(self) -> None:
        if not self.DB_SECRET_JSON:
            return

        try:
            secret = json.loads(self.DB_SECRET_JSON)
        except json.JSONDecodeError as exc:
            raise ValueError("DB_SECRET_JSON is not valid JSON") from exc

        # Standard RDS secret keys
        self.POSTGRES_HOST = secret.get("host", self.POSTGRES_HOST)
        self.POSTGRES_PORT = int(secret.get("port", self.POSTGRES_PORT))
        self.POSTGRES_DB = secret.get("dbname", self.POSTGRES_DB)
        self.POSTGRES_USER = secret.get("username", self.POSTGRES_USER)
        self.POSTGRES_PASSWORD = secret.get("password", self.POSTGRES_PASSWORD)

        # Optional: also override APP_PASSWORD from the same secret if present.
        # If the secret does not contain app_password, keep env APP_PASSWORD.
        self.APP_PASSWORD = secret.get("app_password", self.APP_PASSWORD)

        # Optional: override Salesforce credentials from the same secret
        self.SALESFORCE_CLIENT_ID = secret.get("salesforce_client_id", self.SALESFORCE_CLIENT_ID)
        self.SALESFORCE_CLIENT_SECRET = secret.get("salesforce_client_secret", self.SALESFORCE_CLIENT_SECRET)

    PROCESSING_FILES_DIR: str = os.getenv("PROCESSING_FILES_DIR", str(BASE_DIR / "new_processing_files"))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}")


settings = Settings()

USERNAME = settings.APP_USERNAME
PASSWORD = settings.APP_PASSWORD
OTP_WAIT_SECONDS = settings.OTP_WAIT_SECONDS