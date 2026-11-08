"""Opt-in shared demo using an isolated database and the real evaluator."""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .errors import AppError
from .import_url import valid_organizer_url

ROOT = Path(__file__).resolve().parents[2]
NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "X-Frame-Options": "DENY",
            "Referrer-Policy": "same-origin", "X-Content-Type-Options": "nosniff"}
COOKIE_NAME = "__Host-cqa_demo"
SESSION_SECONDS = 8 * 60 * 60


def organizer_endpoint(value: str):
    """Keep only the trusted destination; never retain its query/signature."""
    if not valid_organizer_url(value):
        return None
    parsed = urlsplit(value)
    if parsed.port not in (None, 443):
        return None
    return parsed.hostname.lower(), parsed.path or "/"


def organizer_import_guard(configured_url: str):
    endpoint = organizer_endpoint(configured_url)

    def guard(request_url: str):
        if not request_url:
            raise AppError(422, "demo_api_url_required", "Вставьте полную ссылку API организаторов в поле выше. В общем демо ссылка не подставляется с сервера.")
        if endpoint is None:
            raise AppError(403, "demo_organizer_not_configured", "Владелец сайта ещё не настроил адрес API организаторов для общего демо.")
        if organizer_endpoint(request_url) != endpoint:
            raise AppError(403, "demo_organizer_url_denied", "Ссылка не соответствует API организаторов, разрешённому на этом сайте. Вставьте полную ссылку из руководства.")

    return guard


class DemoAccess:
    def __init__(self, app, *, password: str, public_access: bool = False):
        if not public_access and len(password) < 20:
            raise RuntimeError("A demo password of at least 20 characters is required")
        self.app = app
        self.password = password.encode("utf-8")
        self.public_access = public_access

    def session_token(self):
        payload = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_urlsafe(24)}"
        signature = hmac.new(self.password, b"cqa-demo-session-v1:" + payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def valid_session(self, headers):
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get("cookie", ""))
            token = cookie[COOKIE_NAME].value
            if len(token) > 256 or not token.isascii():
                return False
            expires, nonce, signature = token.split(".")
            payload = f"{expires}.{nonce}"
            expected = hmac.new(self.password, b"cqa-demo-session-v1:" + payload.encode(), hashlib.sha256).hexdigest()
            return (0 < int(expires) - time.time() <= SESSION_SECONDS
                    and secrets.compare_digest(signature, expected))
        except (KeyError, ValueError, CookieError, UnicodeError):
            return False

    @staticmethod
    def unsafe_origin(scope, headers):
        if scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return False
        if headers.get("sec-fetch-site") == "cross-site":
            return True
        origin = headers.get("origin")
        if origin is None:
            return False  # Non-browser API clients still require valid credentials.
        try:
            parsed = urlsplit(origin)
            return (parsed.scheme != scope.get("scheme", "http")
                    or parsed.netloc.lower() != headers.get("host", "").lower()
                    or bool(parsed.path or parsed.query or parsed.fragment))
        except ValueError:
            return True

    async def login(self, scope, receive, send):
        error = ""
        if scope["method"] == "POST":
            content_type = Headers(scope=scope).get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/x-www-form-urlencoded":
                await JSONResponse({"error": "Use the login form"}, status_code=415,
                                   headers=NO_STORE)(scope, receive, send)
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 4096:
                    await JSONResponse({"error": "Login request too large"}, status_code=413,
                                       headers=NO_STORE)(scope, receive, send)
                    return
                if not message.get("more_body"):
                    break
            try:
                fields = parse_qs(body.decode("utf-8"), max_num_fields=1, errors="strict", keep_blank_values=True)
                supplied = fields["password"][0] if set(fields) == {"password"} else ""
            except (ValueError, UnicodeError):
                supplied = ""
            if secrets.compare_digest(supplied.encode("utf-8"), self.password):
                response = RedirectResponse("/", status_code=303, headers=NO_STORE)
                response.set_cookie(COOKIE_NAME, self.session_token(), max_age=SESSION_SECONDS,
                                    secure=True, httponly=True, samesite="strict", path="/")
                await response(scope, receive, send)
                return
            error = "Неверный пароль. Проверьте пароль команды и попробуйте снова."
        elif scope["method"] != "GET":
            await JSONResponse({"error": "Method not allowed"}, status_code=405,
                               headers=NO_STORE)(scope, receive, send)
            return
        html = (Path(__file__).with_name("demo_login.html")).read_text(encoding="utf-8")
        response = HTMLResponse(html.replace("<!--LOGIN_ERROR-->", error), status_code=401 if error else 200,
                                headers={**NO_STORE, "Content-Security-Policy":
                                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if self.unsafe_origin(scope, headers):
            await JSONResponse({"error": {"code": "cross_origin_denied",
                "message": "Open the demo directly to make changes.", "retryable": False}},
                status_code=403, headers=NO_STORE)(scope, receive, send)
            return
        if scope["path"] == "/demo-login":
            if self.public_access:
                await RedirectResponse("/", status_code=303, headers=NO_STORE)(scope, receive, send)
            else:
                await self.login(scope, receive, send)
            return
        valid = False
        try:
            values = headers.getlist("authorization")
            if len(values) == 1:
                scheme, encoded = values[0].split(" ", 1)
                if scheme.lower() == "basic" and len(encoded) <= 2048:
                    user, password = base64.b64decode(encoded, validate=True).split(b":", 1)
                    user_ok = secrets.compare_digest(user, b"demo")
                    password_ok = secrets.compare_digest(password, self.password)
                    valid = user_ok and password_ok
        except (ValueError, binascii.Error):
            pass
        valid = self.public_access or valid or self.valid_session(headers)
        if (not valid and scope["method"] == "GET" and not scope["path"].startswith("/api/")
                and "text/html" in headers.get("accept", "")):
            await RedirectResponse("/demo-login", status_code=303, headers=NO_STORE)(scope, receive, send)
            return
        if not valid:
            response = JSONResponse({"error": {"code": "demo_login_required",
                "message": "Enter the demo login and password.", "retryable": False}}, status_code=401,
                headers=NO_STORE)
            await response(scope, receive, send)
            return
        async def secure_send(message):
            if message["type"] == "http.response.start":
                replace_names = {k.lower().encode() for k in NO_STORE}
                message = {**message, "headers": [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in replace_names
                ] + [(k.encode(), v.encode()) for k, v in NO_STORE.items()]}
            await send(message)

        await self.app(scope, receive, secure_send)


def create_demo_app():
    # Explicit process setting, read before .env. The launcher sets it from --public.
    public_access = os.environ.get("CQA_DEMO_PUBLIC") == "1"
    password = os.environ.get("CQA_DEMO_PASSWORD", "")
    if not public_access and len(password) < 20:
        raise RuntimeError("Start the protected demo with python -m scripts.run_demo")
    load_dotenv(ROOT / ".env", override=False)
    # Owner-approved exact HTTPS endpoint. The signed URL itself is never a fallback in demo.
    guard = organizer_import_guard(os.getenv("CQA_DEMO_ORGANIZER_ENDPOINT") or os.getenv("DEV_API_URL", ""))
    # Isolate settings BEFORE importing main (which constructs its normal app at import time).
    os.environ["DATABASE_PATH"] = str(ROOT / "data" / "demo" / "cqa.sqlite3")
    os.environ["DEV_API_URL"] = ""
    os.environ["AI_USAGE_LOG_PATH"] = str(ROOT / "data" / "demo" / "ai_usage.jsonl")
    from .main import create_app
    from .settings import Settings
    from .contracts import ImportRequest

    settings = replace(Settings.from_env(), database_path=os.environ["DATABASE_PATH"],
                       dev_api_url="", cors_origins=[], ai_concurrency=1)
    app = create_app(settings=settings, frontend_dist=str(ROOT / "frontend" / "dist"), organizer_url_guard=guard)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner):
        async with original_lifespan(inner):
            service = inner.state.service
            if not service.db.list("conversations", limit=1):
                payload = json.loads((ROOT / "contracts" / "demo-conversations.json").read_text(encoding="utf-8"))
                examples = ImportRequest.model_validate(payload)
                if not all(c.metadata.get("synthetic") is True for c in examples.conversations):
                    raise RuntimeError("Only labelled synthetic examples may seed the shared demo")
                service.import_conversations(examples.conversations)
            yield

    app.router.lifespan_context = lifespan
    app.add_middleware(DemoAccess, password=password, public_access=public_access)
    return app
