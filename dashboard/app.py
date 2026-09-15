"""
Meridian Flow Network operations desk.

Reads scraped_buyers / client_catalog / matched_leads from Supabase
and lets an operator move a lead from draft -> approved or sent.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from meridian.auth import (
    app_base_url,
    authenticate,
    clear_session_cookie,
    client_ip,
    consume_reset_token,
    delete_session,
    desk_auth_enforced,
    ensure_bootstrap_identities,
    forgot_blocked,
    login_blocked,
    portal_auth_enforced,
    portal_home,
    portal_token_for_user,
    public_user,
    request_password_reset,
    request_user,
    safe_next,
    set_session_cookie,
    unauthorized,
    update_password,
    valid_email,
    validate_new_password,
    DESK_COOKIE,
    PORTAL_COOKIE,
)
from meridian.billing import (
    BillingConfigError,
    BillingGatewayError,
    BillingSignatureError,
    access_granted,
    create_customer_portal,
    create_hosted_retainer_session,
    create_retainer_session,
    handle_webhook,
)
from meridian.config import load_settings
from meridian.db import connect, connect_portal
from meridian.mail import MailSendError, send_access_request_email
from meridian.portal_fixtures import REVISION_DRAFT_STATUS, ensure_revision_requested_lead

STATIC = Path(__file__).resolve().parent / "static"
ALLOWED_STATUSES = frozenset({"draft", "approved", "sent", "rejected", "revision_requested"})
PORTAL_STATUSES = frozenset({"approved", "revision_requested"})
StatusValue = Literal["draft", "approved", "sent", "rejected", "revision_requested"]
PUBLIC_PATHS = frozenset({
    "/",
    "/health",
    "/api/inquiries",
    "/api/request-access",
    "/privacy",
    "/terms",
    "/cookies",
    "/desk/login",
    "/desk/forgot",
    "/desk/reset",
    "/portal/login",
    "/portal/forgot",
    "/portal/reset",
    "/portal/demo",
    "/json/version",
    "/json/list",
})
PUBLIC_PREFIXES = (
    "/static/",
    "/api/auth/",
    "/pay/",
    "/api/pay/",
    "/api/portal/demo",
)
TOKEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
OPS_HOST = "ops.meridianflownetwork.com"
PORTAL_HOST = "portal.meridianflownetwork.com"
APP_HOST = "app.meridianflownetwork.com"
ROOT_HOST_REWRITES = {
    OPS_HOST: "/desk",
    PORTAL_HOST: "/portal/demo",
}
APP_PAY_SUCCESS_URL = "https://app.meridianflownetwork.com/success?session_id={CHECKOUT_SESSION_ID}"
APP_PAY_CANCEL_URL = "https://app.meridianflownetwork.com/cancel"
APP_HOST_REWRITES = {
    "/portal": "/portal",
    "/pay/retainer": "/pay/retainer",
    "/pay/retainer/success": "/pay/retainer/success",
    "/pay/retainer/cancel": "/pay/retainer/cancel",
    "/success": "/pay/retainer/success",
    "/cancel": "/pay/retainer/cancel",
}


def _is_public(request: Request) -> bool:
    path = request.url.path
    if path in PUBLIC_PATHS:
        if path == "/api/inquiries":
            return request.method in {"POST", "OPTIONS"}
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _basic_ok(request: Request) -> bool:
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not password:
        return False
    user = os.getenv("DASHBOARD_USER", "meridian")
    incoming = request.headers.get("authorization", "")
    if not incoming.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(incoming.split(" ", 1)[1]).decode("utf-8")
        given_user, given_password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(given_user, user) and secrets.compare_digest(given_password, password)


def _hostname_from_header(host: str) -> str:
    value = (host or "").split(",")[0].strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end]
    if ":" in value:
        return value.rsplit(":", 1)[0]
    return value


def _host_from_scope(scope: dict[str, Any]) -> str:
    for key, value in scope.get("headers") or []:
        if key == b"host":
            return value.decode("latin-1")
    return ""


def _trimmed_path(path: str) -> str:
    path = path or "/"
    if len(path) > 1 and path.endswith("/"):
        return path[:-1]
    return path or "/"


def _rewritten_path(hostname: str, path: str) -> str:
    trimmed = _trimmed_path(path)
    if trimmed == "/portal/demo":
        return "/portal/demo" if path != "/portal/demo" else ""
    if trimmed in {"", "/"}:
        return ROOT_HOST_REWRITES.get(hostname, "")
    if hostname != APP_HOST:
        return ""
    if trimmed.startswith("/portal/") and trimmed != "/portal":
        return ""
    return APP_HOST_REWRITES.get(trimmed, "")


class SubdomainRewriteMiddleware:
    """Host-based path mapping for ops, portal, and app subdomains."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") in {"http", "websocket"}:
            target = _rewritten_path(
                _hostname_from_header(_host_from_scope(scope)),
                scope.get("path") or "/",
            )
            if target:
                scope = {**scope, "path": target, "raw_path": target.encode("ascii")}
        await self.app(scope, receive, send)


def _path_portal_token(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "portal":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "portal":
        return parts[2]
    return ""


class OpsAuthMiddleware(BaseHTTPMiddleware):
    """Session login for desk/portal, with optional HTTP Basic fallback for operators."""

    async def dispatch(self, request: Request, call_next):
        if _is_public(request):
            return await call_next(request)
        path = request.url.path
        if _basic_ok(request):
            return await call_next(request)

        if path.rstrip("/") == "/portal":
            if not portal_auth_enforced() or request_user(request, "portal") or request_user(request, "desk"):
                return await call_next(request)
            return unauthorized(request, login_path="/portal/login", next_path="/portal")

        portal_token = _path_portal_token(path)
        if portal_token:
            if portal_token.lower() == "demo" or not portal_auth_enforced():
                return await call_next(request)
            desk_user = request_user(request, "desk")
            portal_user = request_user(request, "portal")
            if desk_user or (
                portal_user
                and portal_token_for_user(portal_user).lower() == portal_token.lower()
            ):
                return await call_next(request)
            next_path = path if path.startswith("/portal/") else f"/portal/{portal_token}"
            return unauthorized(request, login_path="/portal/login", next_path=next_path)

        if not desk_auth_enforced() or request_user(request, "desk"):
            return await call_next(request)
        next_path = path if path in {"/desk", "/ops"} else "/desk"
        return unauthorized(request, login_path="/desk/login", next_path=next_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        ensure_bootstrap_identities()
    except Exception:
        pass
    yield


app = FastAPI(title="Meridian Flow Network", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(OpsAuthMiddleware)
app.add_middleware(SubdomainRewriteMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class StatusPayload(BaseModel):
    status: StatusValue = Field(..., description="New approval_status")
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""


class ClientSettingsPayload(BaseModel):
    company_name: str | None = None
    sender_name: str | None = None
    reply_to_email: str | None = None
    domain: str | None = None
    product_category: str | None = None
    pipeline_enabled: bool | None = None

    @field_validator("company_name", "sender_name", "reply_to_email", "domain", "product_category", mode="before")
    @classmethod
    def _trim(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return value

    @field_validator("reply_to_email")
    @classmethod
    def _reply(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("reply_to_email must be a valid address")
        return value


class AccessRequestPayload(BaseModel):
    company: str = Field(..., min_length=2, max_length=200)
    your_name: str = Field(..., min_length=2, max_length=160)
    work_email: str = Field(..., min_length=5, max_length=200)
    country: str = ""
    website: str = ""
    product_category: str = ""
    target_regions: list[str] = Field(default_factory=list)
    technical_focus: str = ""
    note_to_the_desk: str = ""
    fax: str = ""

    @field_validator(
        "company",
        "your_name",
        "country",
        "website",
        "product_category",
        "technical_focus",
        "note_to_the_desk",
        "fax",
        mode="before",
    )
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("work_email")
    @classmethod
    def _email(cls, value: str) -> str:
        text = value.strip() if isinstance(value, str) else str(value)
        if "@" not in text or "." not in text.split("@")[-1]:
            raise ValueError("A valid work email is required")
        return text

    @field_validator("target_regions", mode="before")
    @classmethod
    def _regions(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return []


class InquiryPayload(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    contact_name: str = Field(..., min_length=2, max_length=160)
    email: str = Field(..., min_length=5, max_length=200)
    country: str = ""
    website: str = ""
    product_category: str = ""
    target_regions: list[str] = Field(default_factory=list)
    capabilities: str = ""
    message: str = ""
    fax: str = ""

    @field_validator(
        "company_name",
        "contact_name",
        "country",
        "website",
        "product_category",
        "capabilities",
        "message",
        "fax",
        mode="before",
    )
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("A valid work email is required")
        return value

    @field_validator("target_regions", mode="before")
    @classmethod
    def _regions(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return []


class LoginPayload(BaseModel):
    email: str
    password: str
    next: str | None = None

    @field_validator("email", "password", "next", mode="before")
    @classmethod
    def _trim_login(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ForgotPayload(BaseModel):
    email: str
    scope: Literal["desk", "portal"] = "desk"

    @field_validator("email", mode="before")
    @classmethod
    def _trim_email(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ResetPayload(BaseModel):
    token: str
    password: str

    @field_validator("token", "password", mode="before")
    @classmethod
    def _trim_reset(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class RetainerCheckoutPayload(BaseModel):
    access: str
    email: str
    company_name: str = ""

    @field_validator("access", "email", "company_name", mode="before")
    @classmethod
    def _trim_pay(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _pay_email(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("A valid billing email is required")
        return value


class RetainerPortalPayload(BaseModel):
    access: str
    session_id: str = ""

    @field_validator("access", "session_id", mode="before")
    @classmethod
    def _trim_portal(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


def _client():
    return connect(load_settings(require=("SUPABASE_URL", "SUPABASE_KEY")))


def _first_active_portal_token() -> str:
    """Stable demo alias: first pipeline-on house, preferring one that already has matches."""
    db = _client()
    rows = (
        db.table("client_catalog")
        .select("id, client_name, portal_token, pipeline_enabled")
        .order("client_name")
        .execute()
        .data
        or []
    )
    active = [
        row
        for row in rows
        if row.get("portal_token") and row.get("pipeline_enabled") is not False
    ]
    if not active:
        raise HTTPException(status_code=404, detail="No active manufacturer portal")
    leads = (
        db.table("matched_leads")
        .select("client_id")
        .in_("client_id", [row["id"] for row in active])
        .execute()
        .data
        or []
    )
    with_leads = {lead.get("client_id") for lead in leads}
    for row in active:
        if row["id"] in with_leads:
            return row["portal_token"]
    return active[0]["portal_token"]


def _resolve_portal_token(token: str) -> str:
    if (token or "").strip().lower() == "demo":
        return _first_active_portal_token()
    if not TOKEN_RE.match(token or ""):
        raise HTTPException(status_code=404, detail="Not found")
    return token


def _portal_client(token: str):
    return connect_portal(
        load_settings(require=("SUPABASE_URL", "SUPABASE_KEY")),
        _resolve_portal_token(token),
    )


def _portal_profile(db, token: str) -> dict[str, Any]:
    token = _resolve_portal_token(token)
    rows = (
        db.table("client_catalog")
        .select(
            "id, client_name, company_name, sender_name, domain, "
            "product_category, target_regions, portal_token, tenant_id"
        )
        .eq("portal_token", token)
        .limit(2)
        .execute()
        .data
        or []
    )
    if len(rows) != 1:
        raise HTTPException(status_code=404, detail="Not found")
    return rows[0]


def _hydrate(db, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    buyer_ids = list({row["buyer_id"] for row in rows if row.get("buyer_id")})
    client_ids = list({row["client_id"] for row in rows if row.get("client_id")})
    buyers: dict[str, Any] = {}
    if buyer_ids:
        buyers = {
            item["id"]: item
            for item in (
                db.table("scraped_buyers")
                .select("id, company_name, country, target_industry, technical_requirements, status")
                .in_("id", buyer_ids)
                .execute()
                .data
                or []
            )
        }
    manufacturers: dict[str, Any] = {}
    if client_ids:
        manufacturers = {
            item["id"]: item
            for item in (
                db.table("client_catalog")
                .select("id, client_name, company_name, product_category, target_regions")
                .in_("id", client_ids)
                .execute()
                .data
                or []
            )
        }
    payload: list[dict[str, Any]] = []
    for row in rows:
        buyer = buyers.get(row.get("buyer_id")) or {}
        manufacturer = manufacturers.get(row.get("client_id")) or {}
        payload.append(
            {
                "id": row["id"],
                "match_score": float(row.get("match_score") or 0),
                "approval_status": row.get("approval_status") or "draft",
                "outreach_draft": row.get("outreach_draft") or "",
                "revision_note": row.get("revision_note") or "",
                "draft_status": (
                    REVISION_DRAFT_STATUS
                    if (row.get("approval_status") or "") == "revision_requested"
                    else ""
                ),
                "created_at": row.get("created_at"),
                "buyer": {
                    "id": buyer.get("id"),
                    "company_name": buyer.get("company_name") or "Unknown buyer",
                    "country": buyer.get("country") or "",
                    "target_industry": buyer.get("target_industry") or "",
                    "technical_requirements": buyer.get("technical_requirements") or "",
                    "status": buyer.get("status") or "",
                },
                "client": {
                    "id": manufacturer.get("id"),
                    "client_name": manufacturer.get("company_name")
                    or manufacturer.get("client_name")
                    or "Unknown manufacturer",
                    "product_category": manufacturer.get("product_category") or "",
                    "target_regions": manufacturer.get("target_regions") or [],
                },
            }
        )
    return payload


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "meridian-ops-desk"})


@app.get("/")
def landing() -> FileResponse:
    return FileResponse(STATIC / "landing.html")


@app.get("/privacy")
@app.get("/terms")
@app.get("/cookies")
def legal_page() -> FileResponse:
    return FileResponse(STATIC / "legal.html")


@app.get("/desk/login")
@app.get("/desk/forgot")
@app.get("/desk/reset")
def desk_auth_page() -> FileResponse:
    return FileResponse(STATIC / "auth.html")


@app.get("/portal/login")
@app.get("/portal/forgot")
@app.get("/portal/reset")
def portal_auth_page() -> FileResponse:
    return FileResponse(STATIC / "auth.html")


@app.get("/desk")
def desk() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/pay/retainer")
def retainer_checkout_redirect() -> RedirectResponse:
    try:
        session = create_hosted_retainer_session(
            success_url=APP_PAY_SUCCESS_URL,
            cancel_url=APP_PAY_CANCEL_URL,
        )
    except BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}") from e
    url = session.get("url") or ""
    if not url:
        raise HTTPException(status_code=400, detail="Checkout could not be started")
    return RedirectResponse(url=url, status_code=303)


@app.get("/pay/retainer/success")
@app.get("/pay/retainer/cancel")
def retainer_named_page(request: Request) -> FileResponse:
    token = (request.query_params.get("access") or request.query_params.get("token") or "").strip()
    session_id = (request.query_params.get("session_id") or "").strip()
    ending = _trimmed_path(request.url.path).rsplit("/", 1)[-1]
    if ending in {"success", "cancel"} and (access_granted(token) or session_id.startswith("cs_")):
        return FileResponse(STATIC / "pay.html")
    if access_granted(token):
        return FileResponse(STATIC / "pay.html")
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/pay/retainer/{token}")
@app.get("/pay/retainer/{token}/success")
@app.get("/pay/retainer/{token}/cancel")
def retainer_page(token: str) -> FileResponse:
    if not access_granted(token):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(STATIC / "pay.html")


@app.post("/api/auth/desk/login")
def desk_login(payload: LoginPayload, request: Request) -> JSONResponse:
    email = (payload.email or "").lower()
    if login_blocked(email, client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    try:
        result = authenticate(email, payload.password, "desk")
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=503, detail="Sign-in is not available yet")
    destination = safe_next(payload.next, "/desk")
    if not destination.startswith("/desk") and destination != "/ops":
        destination = "/desk"
    response = JSONResponse({"ok": True, "user": public_user(result["user"]), "next": destination})
    set_session_cookie(response, request, result["token"], "desk")
    return response


@app.post("/api/auth/portal/login")
def portal_login(payload: LoginPayload, request: Request) -> JSONResponse:
    email = (payload.email or "").lower()
    if login_blocked(email, client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    try:
        result = authenticate(email, payload.password, "portal")
        home = portal_home(result["user"])
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=503, detail="Sign-in is not available yet")
    destination = safe_next(payload.next, home)
    if destination != "/portal" and not destination.startswith("/portal/"):
        destination = home
    response = JSONResponse({"ok": True, "user": public_user(result["user"]), "next": destination})
    set_session_cookie(response, request, result["token"], "portal")
    return response


@app.post("/api/auth/forgot")
def forgot_password(payload: ForgotPayload, request: Request) -> dict[str, Any]:
    email = (payload.email or "").lower()
    if forgot_blocked(email, client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many reset requests. Try again later.")
    if valid_email(email):
        try:
            request_password_reset(email, payload.scope, request)
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/auth/reset")
def reset_password(payload: ResetPayload) -> dict[str, Any]:
    try:
        password = validate_new_password(payload.password)
        user = consume_reset_token(payload.token)
        update_password(user["id"], password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=503, detail="Password reset is not available yet")
    return {"ok": True, "scope": "desk" if user.get("role") == "operator" else "portal"}


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    delete_session(request.cookies.get(DESK_COOKIE), "desk")
    delete_session(request.cookies.get(PORTAL_COOKIE), "portal")
    response = JSONResponse({"ok": True})
    clear_session_cookie(response, "desk")
    clear_session_cookie(response, "portal")
    return response


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    return {
        "desk": public_user(request_user(request, "desk")),
        "portal": public_user(request_user(request, "portal")),
    }


@app.post("/api/pay/retainer/checkout")
def retainer_checkout(payload: RetainerCheckoutPayload, request: Request) -> dict[str, Any]:
    if not access_granted(payload.access):
        raise HTTPException(status_code=404, detail="Not found")
    base = app_base_url(request)
    token = payload.access
    try:
        session = create_retainer_session(
            email=payload.email,
            company_name=payload.company_name,
            success_url=f"{base}/pay/retainer/{token}/success",
            cancel_url=f"{base}/pay/retainer/{token}/cancel",
        )
    except BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BillingGatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not session.get("url"):
        raise HTTPException(status_code=400, detail="Checkout could not be started")
    return {"url": session["url"]}


@app.post("/api/pay/retainer/portal")
def retainer_portal(payload: RetainerPortalPayload, request: Request) -> dict[str, Any]:
    if not access_granted(payload.access):
        raise HTTPException(status_code=404, detail="Not found")
    token = payload.access
    session_id = payload.session_id
    return_url = f"{app_base_url(request)}/pay/retainer/{token}/success"
    if session_id:
        return_url = f"{return_url}?session_id={session_id}"
    try:
        session = create_customer_portal(session_id=session_id, return_url=return_url)
    except BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BillingGatewayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not session.get("url"):
        raise HTTPException(status_code=400, detail="Invoice portal could not be opened")
    return {"url": session["url"]}


@app.post("/api/pay/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook(payload, signature)
    except BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BillingSignatureError:
        raise HTTPException(status_code=400, detail="Invalid webhook")
    return {"ok": True, "type": result.get("type"), "handled": result.get("handled")}


@app.get("/portal")
@app.get("/portal/")
def portal_entry(request: Request):
    portal_user = request_user(request, "portal")
    if portal_user:
        home = portal_home(portal_user)
        if home.startswith("/portal/") and home not in {"/portal/login", "/portal/demo"}:
            return RedirectResponse(url=home, status_code=303)
    if request_user(request, "desk") or not portal_auth_enforced():
        try:
            token = _first_active_portal_token()
        except HTTPException:
            token = ""
        if token:
            return RedirectResponse(url=f"/portal/{token}", status_code=303)
    return unauthorized(request, login_path="/portal/login", next_path="/portal")


@app.get("/portal/demo")
def portal_demo() -> FileResponse:
    return FileResponse(STATIC / "portal.html")


@app.get("/portal/{token}")
def portal_page(token: str) -> FileResponse:
    if token.lower() != "demo" and not TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(STATIC / "portal.html")


@app.get("/ops")
def ops() -> FileResponse:
    return FileResponse(STATIC / "index.html")


CONFIRM_MESSAGE = "Request received. We review every request by hand."


def _inquiry_fields(payload: InquiryPayload) -> dict[str, Any]:
    return {
        "company": payload.company_name,
        "your_name": payload.contact_name,
        "work_email": payload.email,
        "country": payload.country,
        "website": payload.website,
        "product_category": payload.product_category,
        "target_regions": payload.target_regions,
        "technical_focus": payload.capabilities,
        "note_to_the_desk": payload.message,
    }


def _access_fields(payload: AccessRequestPayload) -> dict[str, Any]:
    return {
        "company": payload.company,
        "your_name": payload.your_name,
        "work_email": payload.work_email,
        "country": payload.country,
        "website": payload.website,
        "product_category": payload.product_category,
        "target_regions": payload.target_regions,
        "technical_focus": payload.technical_focus,
        "note_to_the_desk": payload.note_to_the_desk,
    }


def _inquiry_from_access(payload: AccessRequestPayload) -> InquiryPayload:
    return InquiryPayload(
        company_name=payload.company,
        contact_name=payload.your_name,
        email=payload.work_email,
        country=payload.country,
        website=payload.website,
        product_category=payload.product_category,
        target_regions=payload.target_regions,
        capabilities=payload.technical_focus,
        message=payload.note_to_the_desk,
        fax=payload.fax,
    )


def _store_inquiry(payload: InquiryPayload) -> dict[str, Any] | None:
    row = {
        "company_name": payload.company_name,
        "contact_name": payload.contact_name,
        "email": payload.email,
        "country": payload.country or None,
        "website": payload.website or None,
        "product_category": payload.product_category or None,
        "target_regions": payload.target_regions,
        "capabilities": payload.capabilities or None,
        "message": payload.message or None,
        "status": "pending",
    }
    result = _client().table("pending_inquiries").insert(row).execute()
    return (result.data or [None])[0]


def _email_access_request(fields: dict[str, Any], *, reply_to: str) -> None:
    try:
        send_access_request_email(fields, reply_to=reply_to)
    except MailSendError as exc:
        raise HTTPException(status_code=500, detail=f"SMTP error: {exc}") from exc


async def _read_access_payload(request: Request) -> AccessRequestPayload:
    content_type = (request.headers.get("content-type") or "").lower()
    body = (await request.body()).decode("utf-8", errors="replace")
    if "application/json" in content_type or body.lstrip().startswith(("{", "[")):
        try:
            raw = json.loads(body) if body.strip() else {}
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Expected a JSON object") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")
    else:
        parsed = parse_qs(body, keep_blank_values=True)
        raw = {key: (values[-1] if values else "") for key, values in parsed.items()}
    try:
        return AccessRequestPayload.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@app.post("/api/request-access")
async def request_access(request: Request) -> dict[str, Any]:
    payload = await _read_access_payload(request)
    if payload.fax:
        return {"ok": True, "message": CONFIRM_MESSAGE}
    _email_access_request(_access_fields(payload), reply_to=payload.work_email)
    created: dict[str, Any] | None = None
    try:
        created = _store_inquiry(_inquiry_from_access(payload))
    except Exception:
        created = None
    return {"ok": True, "message": CONFIRM_MESSAGE, "id": (created or {}).get("id")}


@app.post("/api/inquiries")
def create_inquiry(payload: InquiryPayload) -> dict[str, Any]:
    if payload.fax:
        return {"ok": True}
    created = _store_inquiry(payload)
    if not created:
        raise HTTPException(status_code=500, detail="Inquiry was not stored")
    try:
        send_access_request_email(_inquiry_fields(payload), reply_to=payload.email)
    except Exception:
        pass
    return {"ok": True, "id": created.get("id")}


@app.get("/api/inquiries")
def list_inquiries() -> dict[str, Any]:
    db = _client()
    rows = (
        db.table("pending_inquiries")
        .select(
            "id, company_name, contact_name, email, country, website, "
            "product_category, target_regions, capabilities, message, status, created_at"
        )
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data
        or []
    )
    return {"inquiries": rows}


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    db = _client()
    buyers = db.table("scraped_buyers").select("status, signal_type").execute().data or []
    inquiries = db.table("pending_inquiries").select("status").execute().data or []
    leads = (
        db.table("matched_leads")
        .select("approval_status, match_score")
        .execute()
        .data
        or []
    )
    buyer_counts = Counter(row.get("status") or "unknown" for row in buyers)
    signal_counts = Counter(row.get("signal_type") or "buyer_profile" for row in buyers)
    lead_counts = Counter(row.get("approval_status") or "unknown" for row in leads)
    scores = [float(row["match_score"]) for row in leads if row.get("match_score") is not None]
    return {
        "buyers": {
            "total": len(buyers),
            "pending_enrichment": buyer_counts.get("pending_enrichment", 0),
            "enriched": buyer_counts.get("enriched", 0),
            "failed_enrichment": buyer_counts.get("failed_enrichment", 0),
            "tenders": signal_counts.get("foreign_procurement_tender", 0),
            "expansions": signal_counts.get("regional_manufacturing_expansion", 0),
            "filings": signal_counts.get("public_compliance_filing", 0),
        },
        "leads": {
            "total": len(leads),
            "draft": lead_counts.get("draft", 0),
            "approved": lead_counts.get("approved", 0),
            "sent": lead_counts.get("sent", 0),
            "rejected": lead_counts.get("rejected", 0),
            "revision_requested": lead_counts.get("revision_requested", 0),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
        },
        "inquiries": {
            "total": len(inquiries),
            "pending": sum(1 for row in inquiries if (row.get("status") or "pending") == "pending"),
        },
    }


@app.get("/api/leads")
def list_leads() -> dict[str, Any]:
    db = _client()
    rows = (
        db.table("matched_leads")
        .select(
            "id, buyer_id, client_id, match_score, outreach_draft, "
            "approval_status, revision_note, created_at"
        )
        .order("match_score", desc=True)
        .execute()
        .data
        or []
    )
    return {"leads": _hydrate(db, rows)}


@app.post("/api/leads/{lead_id}/status")
def update_status(lead_id: str, payload: StatusPayload) -> dict[str, Any]:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid approval_status")
    db = _client()
    patch: dict[str, Any] = {"approval_status": payload.status}
    if payload.status == "revision_requested":
        patch["revision_note"] = payload.note or None
    elif payload.status == "approved":
        patch["revision_note"] = None
    result = (
        db.table("matched_leads")
        .update(patch)
        .eq("id", lead_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"id": lead_id, "approval_status": payload.status}


def _client_metrics(leads: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row.get("approval_status") or "draft" for row in leads)
    scores = [float(row["match_score"]) for row in leads if row.get("match_score") is not None]
    return {
        "leads": len(leads),
        "draft": counts.get("draft", 0),
        "approved": counts.get("approved", 0),
        "revision_requested": counts.get("revision_requested", 0),
        "sent": counts.get("sent", 0),
        "rejected": counts.get("rejected", 0),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
    }


def _catalog_row(row: dict[str, Any], leads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    metrics = _client_metrics(leads or [])
    return {
        "id": row["id"],
        "client_name": row.get("client_name") or "",
        "company_name": row.get("company_name") or row.get("client_name") or "",
        "sender_name": row.get("sender_name") or "",
        "reply_to_email": row.get("reply_to_email") or "",
        "domain": row.get("domain") or "",
        "product_category": row.get("product_category") or "",
        "target_regions": row.get("target_regions") or [],
        "pipeline_enabled": row.get("pipeline_enabled") is not False,
        "portal_path": f"/portal/{row['portal_token']}" if row.get("portal_token") else "",
        "metrics": metrics,
    }


@app.get("/api/clients")
def list_clients() -> dict[str, Any]:
    db = _client()
    rows = (
        db.table("client_catalog")
        .select(
            "id, client_name, company_name, sender_name, reply_to_email, domain, "
            "product_category, target_regions, portal_token, pipeline_enabled"
        )
        .order("client_name")
        .execute()
        .data
        or []
    )
    leads = db.table("matched_leads").select("client_id, approval_status, match_score").execute().data or []
    by_client: dict[str, list[dict[str, Any]]] = {}
    for lead in leads:
        by_client.setdefault(lead.get("client_id") or "", []).append(lead)
    return {"clients": [_catalog_row(row, by_client.get(row["id"], [])) for row in rows]}


@app.get("/api/clients/{client_id}")
def get_client(client_id: str) -> dict[str, Any]:
    if not TOKEN_RE.match(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    db = _client()
    rows = (
        db.table("client_catalog")
        .select(
            "id, client_name, company_name, sender_name, reply_to_email, domain, "
            "product_category, target_regions, portal_token, pipeline_enabled"
        )
        .eq("id", client_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    leads = (
        db.table("matched_leads")
        .select(
            "id, buyer_id, client_id, match_score, outreach_draft, "
            "approval_status, revision_note, created_at"
        )
        .eq("client_id", client_id)
        .order("match_score", desc=True)
        .execute()
        .data
        or []
    )
    profile = _catalog_row(rows[0], leads)
    profile["leads"] = _hydrate(db, leads)
    return profile


@app.post("/api/clients/{client_id}")
def update_client(client_id: str, payload: ClientSettingsPayload) -> dict[str, Any]:
    if not TOKEN_RE.match(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    patch = payload.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No settings to update")
    db = _client()
    result = db.table("client_catalog").update(patch).eq("id", client_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Client not found")
    return get_client(client_id)


@app.get("/api/portals")
def list_portals() -> dict[str, Any]:
    db = _client()
    rows = (
        db.table("client_catalog")
        .select("id, client_name, company_name, portal_token")
        .order("client_name")
        .execute()
        .data
        or []
    )
    return {
        "portals": [
            {
                "id": row["id"],
                "name": row.get("company_name") or row.get("client_name"),
                "path": f"/portal/{row['portal_token']}",
            }
            for row in rows
            if row.get("portal_token")
        ]
    }


@app.get("/api/portal/{token}")
def portal_payload(token: str) -> dict[str, Any]:
    db = _portal_client(token)
    profile = _portal_profile(db, token)
    try:
        ensure_revision_requested_lead(
            _client(),
            client_id=profile["id"],
            tenant_id=profile.get("tenant_id"),
        )
    except Exception:
        pass
    rows = (
        db.table("matched_leads")
        .select(
            "id, buyer_id, client_id, match_score, outreach_draft, "
            "approval_status, revision_note, created_at"
        )
        .eq("client_id", profile["id"])
        .order("match_score", desc=True)
        .execute()
        .data
        or []
    )
    return {
        "brand": {
            "company_name": profile.get("company_name") or profile.get("client_name"),
            "sender_name": profile.get("sender_name") or "Introductions",
            "domain": profile.get("domain") or "",
            "product_category": profile.get("product_category") or "",
        },
        "leads": _hydrate(db, rows),
    }


@app.post("/api/portal/{token}/leads/{lead_id}/status")
def portal_update_status(token: str, lead_id: str, payload: StatusPayload) -> dict[str, Any]:
    if payload.status not in PORTAL_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    db = _portal_client(token)
    profile = _portal_profile(db, token)
    existing = (
        db.table("matched_leads")
        .select("id, approval_status")
        .eq("id", lead_id)
        .eq("client_id", profile["id"])
        .execute()
        .data
        or []
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Lead not found")
    current = existing[0].get("approval_status") or "draft"
    if current == "sent":
        raise HTTPException(status_code=409, detail="This introduction has already been sent")
    patch: dict[str, Any] = {"approval_status": payload.status}
    if payload.status == "revision_requested":
        patch["revision_note"] = payload.note or None
    else:
        patch["revision_note"] = None
    result = (
        db.table("matched_leads")
        .update(patch)
        .eq("id", lead_id)
        .eq("client_id", profile["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"id": lead_id, "approval_status": payload.status}
