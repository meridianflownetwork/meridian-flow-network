"""Outbound mail via Zoho SMTP or generic SMTP_* settings."""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
import traceback
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from meridian.brand import error as log_error
from meridian.brand import info, warn

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_ACCESS_TO = "eli.mulla@meridianflownetwork.com"
DEFAULT_ZOHO_HOST = "smtp.zoho.eu"
ACCESS_REQUEST_FIELDS = (
    ("company", "Company"),
    ("your_name", "Your name"),
    ("work_email", "Work email"),
    ("country", "Country"),
    ("website", "Website"),
    ("product_category", "Product category"),
    ("target_regions", "Target regions"),
    ("technical_focus", "Technical focus"),
    ("note_to_the_desk", "Note to the desk"),
)
_FIELD_ALIASES = {
    "company": ("company", "company_name"),
    "your_name": ("your_name", "contact_name"),
    "work_email": ("work_email", "email"),
    "country": ("country",),
    "website": ("website",),
    "product_category": ("product_category",),
    "target_regions": ("target_regions",),
    "technical_focus": ("technical_focus", "capabilities"),
    "note_to_the_desk": ("note_to_the_desk", "message"),
}


class MailConfigError(RuntimeError):
    """SMTP host or credentials are missing."""


class MailSendError(RuntimeError):
    """The SMTP server rejected the message."""


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _mail_log(message: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    prefix = "[mail:error]" if err else "[mail]"
    print(f"{prefix} {message}", file=stream, flush=True)
    if err:
        log_error("mail", message)
    else:
        info("mail", message)


def smtp_settings() -> dict[str, str | int | bool]:
    user = _env("ZOHO_USER", "SMTP_USER")
    password = os.getenv("ZOHO_PASS") or os.getenv("ZOHO_PASSWORD") or os.getenv("SMTP_PASSWORD") or ""
    host = _env("SMTP_HOST", "ZOHO_HOST")
    if not host and user:
        lowered = user.lower()
        host = "smtp.zoho.com" if lowered.endswith("@zoho.com") else DEFAULT_ZOHO_HOST
    port_raw = _env("SMTP_PORT", "ZOHO_PORT") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    sender = _env("SMTP_FROM", "ZOHO_FROM") or user
    use_ssl = port == 465 or _env("SMTP_SSL", "ZOHO_SSL").lower() in {"1", "true", "yes"}
    starttls = not use_ssl and _env("SMTP_STARTTLS", "ZOHO_STARTTLS") not in {"0", "false", "no"}
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "sender": sender,
        "use_ssl": use_ssl,
        "starttls": starttls,
    }


def mail_configured() -> bool:
    settings = smtp_settings()
    return bool(settings["host"] and settings["user"] and settings["password"])


def format_key_value_body(fields: Mapping[str, object], *, heading: str = "") -> str:
    lines: list[str] = []
    if heading:
        lines.append(heading)
        lines.append("")
    for key, value in fields.items():
        if isinstance(value, (list, tuple)):
            rendered = ", ".join(str(part) for part in value if str(part).strip())
        elif value is None:
            rendered = ""
        else:
            rendered = str(value).strip()
        if not rendered:
            rendered = "—"
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines) + "\n"


def send_plain_email(
    *,
    to_addr: str,
    subject: str,
    body: str,
    reply_to: str = "",
) -> None:
    settings = smtp_settings()
    host = str(settings["host"])
    user = str(settings["user"])
    password = str(settings["password"])
    sender = str(settings["sender"]) or user
    port = int(settings["port"])
    if not host or not user or not password:
        _mail_log(
            "SMTP is not configured — need ZOHO_USER/ZOHO_PASS or SMTP_USER/SMTP_PASSWORD",
            err=True,
        )
        raise MailConfigError("SMTP is not configured")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_addr
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    mode = "SMTP_SSL" if settings["use_ssl"] else ("SMTP+STARTTLS" if settings["starttls"] else "SMTP")
    _mail_log(
        f"Attempting SMTP connect to {host}:{port} ({mode}) as {user} from {sender}"
    )
    try:
        if settings["use_ssl"]:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                _mail_log(f"Connected to {host}:{port} over SSL")
                smtp.login(user, password)
                _mail_log("Auth success")
                _mail_log(f"Sending mail to {to_addr}")
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                _mail_log(f"Connected to {host}:{port}")
                if settings["starttls"]:
                    _mail_log("Starting TLS")
                    smtp.starttls(context=ssl.create_default_context())
                    _mail_log("STARTTLS success")
                smtp.login(user, password)
                _mail_log("Auth success")
                _mail_log(f"Sending mail to {to_addr}")
                smtp.send_message(message)
    except MailConfigError:
        raise
    except Exception as exc:
        _mail_log(f"SMTP failure at {host}:{port}: {type(exc).__name__}: {exc}", err=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise MailSendError(str(exc)) from exc
    _mail_log(f"Sent '{subject}' to {to_addr}")


def access_request_recipient() -> str:
    return _env("REQUEST_ACCESS_TO") or DEFAULT_ACCESS_TO


def access_request_fields(fields: Mapping[str, object]) -> dict[str, object]:
    ordered: dict[str, object] = {}
    for key, label in ACCESS_REQUEST_FIELDS:
        value: object = ""
        for alias in _FIELD_ALIASES.get(key, (key,)):
            if alias in fields and fields[alias] not in (None, ""):
                value = fields[alias]
                break
        ordered[label] = value
    return ordered


def _print_mock_access_email(*, to_addr: str, subject: str, body: str, reply_to: str = "") -> None:
    warn(
        "mail",
        "SMTP credentials missing (ZOHO_USER/ZOHO_PASS or SMTP_*) — "
        "logging the access-request email instead of sending",
    )
    print("----- access request email (mock) -----")
    print(f"To: {to_addr}")
    print(f"Subject: {subject}")
    if reply_to:
        print(f"Reply-To: {reply_to}")
    print("")
    print(body.rstrip())
    print("----- end mock email -----")


def send_access_request_email(fields: Mapping[str, object], *, reply_to: str = "") -> bool:
    labelled = access_request_fields(fields)
    company = str(labelled.get("Company") or "").strip()
    subject = f"Request Access — {company}" if company else "Request Access"
    body = format_key_value_body(labelled, heading="Request Access submission")
    to_addr = access_request_recipient()
    settings = smtp_settings()
    _mail_log(
        "Access-request dispatch: "
        f"to={to_addr} user_set={bool(settings['user'])} "
        f"password_set={bool(settings['password'])} "
        f"host={settings['host'] or 'unset'}:{settings['port']}"
    )
    if not mail_configured():
        _print_mock_access_email(to_addr=to_addr, subject=subject, body=body, reply_to=reply_to)
        return False
    send_plain_email(
        to_addr=to_addr,
        subject=subject,
        body=body,
        reply_to=reply_to,
    )
    return True
