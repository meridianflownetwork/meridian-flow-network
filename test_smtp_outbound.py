"""Send one outbound message through the Zoho SMTP settings in .env."""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

SENDER = "hello@meridianflownetwork.com"
RECIPIENT = "eli.mulla@meridianflownetwork.com"
SUBJECT = "Meridian Flow Network - Outbound SMTP Test"


def log(message: str) -> None:
    print(f"[smtp-test] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[smtp-test:error] {message}", file=sys.stderr, flush=True)


def main() -> int:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)

    host = os.getenv("SMTP_HOST", "smtp.zoho.eu").strip() or "smtp.zoho.eu"
    port_raw = os.getenv("SMTP_PORT", "587").strip() or "587"
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("DEFAULT_SENDER", "").strip() or SENDER

    try:
        port = int(port_raw)
    except ValueError:
        fail(f"SMTP_PORT is not a number: {port_raw}")
        return 1

    missing = [
        name
        for name, value in (
            ("SMTP_HOST", host),
            ("SMTP_USER", user),
            ("SMTP_PASSWORD", password),
        )
        if not str(value).strip()
    ]
    if missing:
        fail("Missing environment variables: " + ", ".join(missing))
        return 1
    if sender.lower() != SENDER:
        fail(f"DEFAULT_SENDER is {sender}, expected {SENDER}")
        return 1

    use_ssl = port == 465 or os.getenv("SMTP_SSL", "").strip().lower() in {"1", "true", "yes"}
    starttls = not use_ssl and os.getenv("SMTP_STARTTLS", "1").strip().lower() not in {"0", "false", "no"}
    mode = "SSL" if use_ssl else ("STARTTLS" if starttls else "plain")

    log(f"Loaded {env_path.name}")
    log(f"Connecting to {host}:{port} via {mode} as {user}")
    log(f"From {sender} to {RECIPIENT}")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = RECIPIENT
    message["Subject"] = SUBJECT
    message.set_content(
        "This is an outbound SMTP test from Meridian Flow Network.\n\n"
        f"Sender: {sender}\n"
        f"Server: {host}:{port} ({mode})\n"
    )

    context = ssl.create_default_context()
    try:
        if use_ssl:
            log(f"Opening SSL connection to {host}:{port}")
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                log("Connected")
                log("Authenticating")
                smtp.login(user, password)
                log("Authentication succeeded")
                log(f"Sending '{SUBJECT}'")
                smtp.send_message(message)
        else:
            log(f"Opening SMTP connection to {host}:{port}")
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                log("Connected")
                if starttls:
                    log("Starting TLS")
                    smtp.starttls(context=context)
                    log("STARTTLS succeeded")
                log("Authenticating")
                smtp.login(user, password)
                log("Authentication succeeded")
                log(f"Sending '{SUBJECT}'")
                smtp.send_message(message)
    except Exception as exc:
        fail(f"SMTP failure during {mode} to {host}:{port}: {type(exc).__name__}: {exc}")
        return 1

    log(f"Sent '{SUBJECT}' from {sender} to {RECIPIENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
