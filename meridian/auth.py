"""Email/password sessions and password recovery for desk and portal."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Literal
from urllib.parse import urljoin

import bcrypt
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from meridian.brand import info, warn
from meridian.config import load_settings
from meridian.db import connect

Scope = Literal["desk", "portal"]
Role = Literal["operator", "portal"]

DESK_COOKIE = "mfn_desk"
PORTAL_COOKIE = "mfn_portal"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PATH_RE = re.compile(r"^/[^\s]*$")
MIN_PASSWORD = 10

_login_hits: dict[str, list[float]] = {}
_forgot_hits: dict[str, list[float]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or ""))


def safe_next(value: str | None, default: str) -> str:
    text = (value or "").strip()
    if text.startswith("/") and not text.startswith("//") and "://" not in text and PATH_RE.match(text):
        return text
    return default


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], stored.encode("ascii"))
    except ValueError:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def app_base_url(request: Request | None = None) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8765"


def cookie_secure(request: Request) -> bool:
    if os.getenv("AUTH_COOKIE_SECURE", "").strip() in {"1", "true", "yes"}:
        return True
    return request.url.scheme == "https"


def _client():
    return connect(load_settings(require=("SUPABASE_URL", "SUPABASE_KEY")))


def _prune(bucket: dict[str, list[float]], key: str, window: float) -> list[float]:
    cutoff = time.time() - window
    hits = [stamp for stamp in bucket.get(key, []) if stamp > cutoff]
    bucket[key] = hits
    return hits


def rate_limited(bucket: dict[str, list[float]], key: str, *, limit: int, window: float) -> bool:
    hits = _prune(bucket, key, window)
    if len(hits) >= limit:
        return True
    hits.append(time.time())
    bucket[key] = hits
    return False


def login_blocked(email: str, ip: str) -> bool:
    return rate_limited(_login_hits, f"{ip}:{email}", limit=8, window=15 * 60)


def forgot_blocked(email: str, ip: str) -> bool:
    return rate_limited(_forgot_hits, f"{ip}:{email}", limit=5, window=60 * 60)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def tables_ready() -> bool:
    try:
        _client().table("platform_users").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def find_user_by_email(email: str) -> dict[str, Any] | None:
    rows = (
        _client()
        .table("platform_users")
        .select("id, email, password_hash, role, client_id, display_name")
        .eq("email", normalize_email(email))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def find_user_by_id(user_id: str) -> dict[str, Any] | None:
    rows = (
        _client()
        .table("platform_users")
        .select("id, email, password_hash, role, client_id, display_name")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def create_user(
    *,
    email: str,
    password: str,
    role: Role,
    client_id: str | None = None,
    display_name: str = "",
) -> dict[str, Any]:
    payload = {
        "email": normalize_email(email),
        "password_hash": hash_password(password),
        "role": role,
        "client_id": client_id,
        "display_name": display_name or None,
    }
    created = (_client().table("platform_users").insert(payload).execute().data or [None])[0]
    if not created:
        raise RuntimeError("User was not stored")
    return created


def update_password(user_id: str, password: str) -> None:
    result = (
        _client()
        .table("platform_users")
        .update({"password_hash": hash_password(password)})
        .eq("id", user_id)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Password was not updated")


def operator_count() -> int:
    rows = _client().table("platform_users").select("id").eq("role", "operator").execute().data or []
    return len(rows)


def portal_user_count() -> int:
    rows = _client().table("platform_users").select("id").eq("role", "portal").execute().data or []
    return len(rows)


def desk_auth_enforced() -> bool:
    if not tables_ready():
        return False
    try:
        return operator_count() > 0
    except Exception:
        return False


def portal_auth_enforced() -> bool:
    if not tables_ready():
        return False
    try:
        return portal_user_count() > 0
    except Exception:
        return False


def create_session(user_id: str, scope: Scope, *, hours: int) -> str:
    token = new_token()
    expires = _now() + timedelta(hours=hours)
    _client().table("auth_sessions").insert(
        {
            "user_id": user_id,
            "token_hash": hash_token(token),
            "scope": scope,
            "expires_at": _iso(expires),
        }
    ).execute()
    return token


def session_user(token: str | None, scope: Scope) -> dict[str, Any] | None:
    if not token:
        return None
    rows = (
        _client()
        .table("auth_sessions")
        .select("id, user_id, scope, expires_at")
        .eq("token_hash", hash_token(token))
        .eq("scope", scope)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    row = rows[0]
    expires = row.get("expires_at") or ""
    try:
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return None
    if expiry <= _now():
        _client().table("auth_sessions").delete().eq("id", row["id"]).execute()
        return None
    user = find_user_by_id(row["user_id"])
    if not user:
        return None
    return {**user, "session_id": row["id"]}


def delete_session(token: str | None, scope: Scope) -> None:
    if not token:
        return
    _client().table("auth_sessions").delete().eq("token_hash", hash_token(token)).eq("scope", scope).execute()


def request_user(request: Request, scope: Scope) -> dict[str, Any] | None:
    cookie = DESK_COOKIE if scope == "desk" else PORTAL_COOKIE
    try:
        return session_user(request.cookies.get(cookie), scope)
    except Exception:
        return None


def set_session_cookie(response: Response, request: Request, token: str, scope: Scope) -> None:
    hours = 12 if scope == "desk" else 24 * 7
    name = DESK_COOKIE if scope == "desk" else PORTAL_COOKIE
    response.set_cookie(
        key=name,
        value=token,
        max_age=hours * 3600,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, scope: Scope) -> None:
    name = DESK_COOKIE if scope == "desk" else PORTAL_COOKIE
    response.delete_cookie(key=name, path="/")


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "role": user.get("role"),
        "client_id": user.get("client_id"),
        "display_name": user.get("display_name") or "",
    }


def authenticate(email: str, password: str, scope: Scope) -> dict[str, Any]:
    user = find_user_by_email(email)
    if not user or not verify_password(password, user.get("password_hash") or ""):
        raise PermissionError("Invalid email or password")
    role = user.get("role")
    if scope == "desk" and role != "operator":
        raise PermissionError("Invalid email or password")
    if scope == "portal" and role != "portal":
        raise PermissionError("Invalid email or password")
    hours = 12 if scope == "desk" else 24 * 7
    token = create_session(user["id"], scope, hours=hours)
    return {"user": user, "token": token}


def create_reset_token(user_id: str) -> str:
    token = new_token()
    _client().table("password_reset_tokens").insert(
        {
            "user_id": user_id,
            "token_hash": hash_token(token),
            "expires_at": _iso(_now() + timedelta(hours=1)),
        }
    ).execute()
    return token


def consume_reset_token(token: str) -> dict[str, Any]:
    rows = (
        _client()
        .table("password_reset_tokens")
        .select("id, user_id, expires_at, used_at")
        .eq("token_hash", hash_token(token))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise PermissionError("This reset link is invalid or has expired")
    row = rows[0]
    if row.get("used_at"):
        raise PermissionError("This reset link is invalid or has expired")
    try:
        expiry = datetime.fromisoformat(str(row.get("expires_at") or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermissionError("This reset link is invalid or has expired") from exc
    if expiry <= _now():
        raise PermissionError("This reset link is invalid or has expired")
    _client().table("password_reset_tokens").update({"used_at": _iso(_now())}).eq("id", row["id"]).execute()
    user = find_user_by_id(row["user_id"])
    if not user:
        raise PermissionError("This reset link is invalid or has expired")
    return user


def send_reset_email(*, to_addr: str, reset_url: str, scope: Scope) -> None:
    subject = "Reset your password"
    body = (
        "A password reset was requested for this sign-in.\n\n"
        f"Open this link within one hour:\n{reset_url}\n\n"
        "If you did not request this, you can ignore the message.\n"
    )
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        info("auth", f"SMTP unset — password reset link for {to_addr}: {reset_url}")
        return
    message = EmailMessage()
    sender = os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_USER", "").strip() or "noreply@localhost"
    message["From"] = sender
    message["To"] = to_addr
    message["Subject"] = subject
    message.set_content(body)
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_STARTTLS", "1").strip() not in {"0", "false", "no"}
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)
    info("auth", f"Password reset sent ({scope})")


def request_password_reset(email: str, scope: Scope, request: Request) -> None:
    user = find_user_by_email(email)
    if not user:
        return
    role = user.get("role")
    if scope == "desk" and role != "operator":
        return
    if scope == "portal" and role != "portal":
        return
    token = create_reset_token(user["id"])
    prefix = "/desk/reset" if scope == "desk" else "/portal/reset"
    reset_url = urljoin(app_base_url(request) + "/", f"{prefix.lstrip('/')}?token={token}")
    send_reset_email(to_addr=user["email"], reset_url=reset_url, scope=scope)


def validate_new_password(password: str) -> str:
    text = password or ""
    if len(text) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")
    if text.isalpha() or text.isdigit():
        raise ValueError("Password must include letters and numbers")
    return text


def portal_home(user: dict[str, Any]) -> str:
    client_id = user.get("client_id")
    if not client_id:
        return "/portal/login"
    rows = (
        _client()
        .table("client_catalog")
        .select("portal_token")
        .eq("id", client_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    token = (rows[0] or {}).get("portal_token") if rows else ""
    return f"/portal/{token}" if token else "/portal/login"


def portal_token_for_user(user: dict[str, Any] | None) -> str:
    if not user or user.get("role") != "portal" or not user.get("client_id"):
        return ""
    rows = (
        _client()
        .table("client_catalog")
        .select("portal_token")
        .eq("id", user["client_id"])
        .limit(1)
        .execute()
        .data
        or []
    )
    return str((rows[0] or {}).get("portal_token") or "") if rows else ""


def _slug_email(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", (name or "client").lower()).strip(".")
    return f"{slug or 'client'}@portal.local"


def ensure_bootstrap_identities() -> None:
    """Create the first operator / portal logins from env without overwriting passwords."""
    if not tables_ready():
        warn("auth", "platform_users is not reachable — login pages will stay available")
        return
    db = _client()
    operator_email = normalize_email(
        os.getenv("OPERATOR_EMAIL", "") or os.getenv("DASHBOARD_EMAIL", "")
    )
    operator_password = os.getenv("OPERATOR_PASSWORD", "") or os.getenv("DASHBOARD_PASSWORD", "")
    if operator_email and operator_password and valid_email(operator_email):
        existing = find_user_by_email(operator_email)
        if not existing:
            create_user(
                email=operator_email,
                password=operator_password,
                role="operator",
                display_name=os.getenv("DASHBOARD_USER", "Operations"),
            )
            info("auth", f"Operator login ready · {operator_email}")
    portal_password = os.getenv("PORTAL_DEFAULT_PASSWORD", "")
    if not portal_password:
        return
    clients = (
        db.table("client_catalog")
        .select("id, client_name, company_name, reply_to_email")
        .execute()
        .data
        or []
    )
    existing_portal = {
        row.get("client_id")
        for row in (db.table("platform_users").select("client_id").eq("role", "portal").execute().data or [])
        if row.get("client_id")
    }
    created = 0
    for row in clients:
        if row["id"] in existing_portal:
            continue
        reply = normalize_email(row.get("reply_to_email") or "")
        email = reply if valid_email(reply) else _slug_email(row.get("company_name") or row.get("client_name") or "")
        if find_user_by_email(email):
            email = _slug_email(f"{row.get('client_name') or row['id']}")
        try:
            create_user(
                email=email,
                password=portal_password,
                role="portal",
                client_id=row["id"],
                display_name=row.get("company_name") or row.get("client_name") or "",
            )
            created += 1
        except Exception as exc:
            warn("auth", f"Portal login skipped for {row.get('client_name')}: {exc}")
    if created:
        info("auth", f"Portal logins ready · {created} house(s)")


def unauthorized(request: Request, *, login_path: str, next_path: str) -> Response:
    wants_html = "text/html" in (request.headers.get("accept") or "") or not request.url.path.startswith("/api/")
    if request.method == "GET" and wants_html:
        return RedirectResponse(url=f"{login_path}?next={next_path}", status_code=303)
    return JSONResponse({"detail": "Authentication required"}, status_code=401)
