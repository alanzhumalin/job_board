import asyncio
import logging
import smtplib
from email.message import EmailMessage
from html import escape

try:
    import resend
except ImportError:  # pragma: no cover - safe fallback if dependency isn't installed yet
    resend = None

from app.config import get_settings
from app.models import Application, Job

logger = logging.getLogger(__name__)
settings = get_settings()


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


def _build_subject(job: Job) -> str:
    return f"Application received: {job.title}"


def _build_text_body(application: Application, job: Job) -> str:
    return "\n".join(
        [
            f"Hi {application.full_name},",
            "",
            f"We received your application for {job.title} at {job.company}.",
            "Our team will review it and follow up if there is a fit.",
            "",
            "Thanks for applying.",
        ]
    )


def _build_html_body(application: Application, job: Job) -> str:
    safe_name = escape(application.full_name)
    safe_title = escape(job.title)
    safe_company = escape(job.company)
    return f"""
    <div style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6;">
      <p>Hi {safe_name},</p>
      <p>We received your application for <strong>{safe_title}</strong> at <strong>{safe_company}</strong>.</p>
      <p>Our team will review it and follow up if there is a fit.</p>
      <p>Thanks for applying.</p>
    </div>
    """.strip()


def _send_via_resend_sync(application: Application, job: Job) -> None:
    if resend is None:
        raise RuntimeError("resend package is not installed.")

    resend.api_key = settings.resend_api_key
    params: resend.Emails.SendParams = {
        "from": settings.resend_from_email,
        "to": [application.email],
        "subject": _build_subject(job),
        "html": _build_html_body(application, job),
        "text": _build_text_body(application, job),
    }
    resend.Emails.send(params)


def _send_via_smtp_sync(application: Application, job: Job) -> None:
    message = EmailMessage()
    message["Subject"] = _build_subject(job)
    message["From"] = settings.smtp_from_email
    message["To"] = application.email
    message.set_content(_build_text_body(application, job))
    message.add_alternative(_build_html_body(application, job), subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def send_application_confirmation(application: Application, job: Job) -> None:
    if resend_is_configured():
        logger.info(
            "Sending confirmation email via Resend for application_id=%s email=%s",
            application.id,
            application.email,
        )
        try:
            await asyncio.to_thread(_send_via_resend_sync, application, job)
            return
        except Exception:
            logger.exception(
                "Failed to send confirmation email via Resend for application_id=%s",
                application.id,
            )

    if smtp_is_configured():
        logger.info(
            "Sending confirmation email via SMTP for application_id=%s email=%s",
            application.id,
            application.email,
        )
        try:
            await asyncio.to_thread(_send_via_smtp_sync, application, job)
            return
        except Exception:
            logger.exception(
                "Failed to send confirmation email via SMTP for application_id=%s",
                application.id,
            )
        return

    logger.info(
        "Email provider not configured, logging confirmation for application_id=%s email=%s job_id=%s",
        application.id,
        application.email,
        job.id,
    )
