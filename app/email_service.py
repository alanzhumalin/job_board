import asyncio
import logging
import smtplib
from email.message import EmailMessage
from html import escape
from typing import TypedDict

try:
    import resend
except ImportError:  # pragma: no cover - safe fallback if dependency isn't installed yet
    resend = None

from app.config import get_settings
from app.models import Application, Job

logger = logging.getLogger(__name__)
settings = get_settings()


class ConfirmationEmailPayload(TypedDict):
    application_id: int
    applicant_name: str
    applicant_email: str
    job_id: int
    job_title: str
    company: str


def resend_is_configured() -> bool:
    required_values = [
        settings.resend_api_key,
        settings.resend_from_email,
    ]
    return all(required_values)


def smtp_is_configured() -> bool:
    required_values = [
        settings.smtp_host,
        settings.smtp_username,
        settings.smtp_password,
        settings.smtp_from_email,
    ]
    return all(required_values)


def _build_subject(payload: ConfirmationEmailPayload) -> str:
    return f"Application received: {payload['job_title']}"


def _build_text_body(payload: ConfirmationEmailPayload) -> str:
    return "\n".join(
        [
            f"Hi {payload['applicant_name']},",
            "",
            (
                "We received your application for "
                f"{payload['job_title']} at {payload['company']}."
            ),
            "Our team will review it and follow up if there is a fit.",
            "",
            "Thanks for applying.",
        ]
    )


def _build_html_body(payload: ConfirmationEmailPayload) -> str:
    safe_name = escape(payload["applicant_name"])
    safe_title = escape(payload["job_title"])
    safe_company = escape(payload["company"])
    return f"""
    <div style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6;">
      <p>Hi {safe_name},</p>
      <p>We received your application for <strong>{safe_title}</strong> at <strong>{safe_company}</strong>.</p>
      <p>Our team will review it and follow up if there is a fit.</p>
      <p>Thanks for applying.</p>
    </div>
    """.strip()


def _send_via_resend_sync(payload: ConfirmationEmailPayload) -> None:
    if resend is None:
        raise RuntimeError("resend package is not installed.")

    resend.api_key = settings.resend_api_key
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [payload["applicant_email"]],
        "subject": _build_subject(payload),
        "html": _build_html_body(payload),
        "text": _build_text_body(payload),
    }
    resend.Emails.send(params)


def _send_via_smtp_sync(payload: ConfirmationEmailPayload) -> None:
    message = EmailMessage()
    message["Subject"] = _build_subject(payload)
    message["From"] = settings.smtp_from_email
    message["To"] = payload["applicant_email"]
    message.set_content(_build_text_body(payload))
    message.add_alternative(_build_html_body(payload), subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def send_application_confirmation(application: Application, job: Job) -> None:
    payload: ConfirmationEmailPayload = {
        "application_id": application.id,
        "applicant_name": application.full_name,
        "applicant_email": application.email,
        "job_id": job.id,
        "job_title": job.title,
        "company": job.company,
    }

    if resend_is_configured():
        logger.info(
            "Sending confirmation email via Resend for application_id=%s email=%s",
            payload["application_id"],
            payload["applicant_email"],
        )
        try:
            await asyncio.to_thread(_send_via_resend_sync, payload)
            return
        except Exception:
            logger.exception(
                "Failed to send confirmation email via Resend for application_id=%s",
                payload["application_id"],
            )

    if smtp_is_configured():
        logger.info(
            "Sending confirmation email via SMTP for application_id=%s email=%s",
            payload["application_id"],
            payload["applicant_email"],
        )
        try:
            await asyncio.to_thread(_send_via_smtp_sync, payload)
            return
        except Exception:
            logger.exception(
                "Failed to send confirmation email via SMTP for application_id=%s",
                payload["application_id"],
            )
        return

    logger.info(
        "Email provider not configured, logging confirmation for application_id=%s email=%s job_id=%s",
        payload["application_id"],
        payload["applicant_email"],
        payload["job_id"],
    )
