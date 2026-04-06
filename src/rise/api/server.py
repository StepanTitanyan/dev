from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import logging

from rise.api.auth import verify_api_token
from rise.api.controllers.application import router as application_router
from rise.api.admin.router import router as admin_router
from rise.api.admin.auth import BasicAuthMiddleware
from rise.otp.webhook import router as otp_router
from rise.db.session import Base, engine
from rise.config.config import setup_logging, settings

logger = logging.getLogger(__name__)

# Paths exempt from the API token check (public, admin, and docs — auth handled elsewhere)
EXCLUDED_PATHS = {"/health", "/sms", "/docs", "/openapi.json", "/redoc", "/admin"}


class ApiTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in EXCLUDED_PATHS):
            return await call_next(request)
        token = request.headers.get("x-api-token", "")
        if not token or not verify_api_token(token):
            logger.warning("Rejected request — invalid or missing x-api-token: path=%s", request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    Base.metadata.create_all(bind=engine)
    logger.info("[STARTUP] Database tables initialised")
    yield


app = FastAPI(
    title="Rise API",
    description="Receives Salesforce application payloads and orchestrates lender submission workflows.",
    version="2.0.0",
    lifespan=lifespan)

# Middleware is LIFO: last added runs first.
# BasicAuthMiddleware executes first (gates /admin and /docs).
# ApiTokenMiddleware executes second (gates API routes).
app.add_middleware(ApiTokenMiddleware)
app.add_middleware(BasicAuthMiddleware,
                   username=settings.ADMIN_USERNAME,
                   password=settings.ADMIN_PASSWORD)

app.include_router(application_router)
app.include_router(otp_router)
app.include_router(admin_router)


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-token"}}
    schema["security"] = [{"ApiKeyAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi
