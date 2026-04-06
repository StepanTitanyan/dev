import base64
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

PROTECTED_PREFIXES = ("/admin", "/docs", "/redoc", "/openapi.json")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str):
        super().__init__(app)
        self._username = username
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if not any(request.url.path.startswith(p) for p in PROTECTED_PREFIXES):
            return await call_next(request)

        if not self._username:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return _unauth()

        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, pw = decoded.split(":", 1)
        except Exception:
            return _unauth()

        if user != self._username or pw != self._password:
            return _unauth()

        return await call_next(request)


def _unauth():
    return Response(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Rise Admin"'})
