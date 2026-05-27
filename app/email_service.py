import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings
from app.models import Application, Job

logger = logging.getLogger(__name__)
settings = get_settings()


def smtp_is_configured() -> bool:
    required_values = [
        settings.smtp_host,
        settings.smtp_username,
        settings.smtp_password,
        settings.smtp_from_email,
    ]
    return all(required_values)


def _send_email_sync(application: Application, job: Job) -> None:
    message = EmailMessage()
    message["Subject"] = f"Application received for {job.title}"
    message["From"] = settings.smtp_from_email
    message["To"] = application.email
    message.set_content(
        "\n".join(
            [
                f"Hi {application.full_name},",
                "",
                f"Thanks for applying to {job.title} at {job.company}.",
                "We received your application and will review it soon.",
                "",
                "This is an automated confirmation email.",
            ]
        )
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def send_application_confirmation(application: Application, job: Job) -> None:
    if not smtp_is_configured():
        logger.info(
            "SMTP not configured. Confirmation email skipped for application_id=%s email=%s job_id=%s",
            application.id,
            application.email,
            job.id,
        )
        return

    try:
        await asyncio.to_thread(_send_email_sync, application, job)
        logger.info(
            "Confirmation email sent for application_id=%s email=%s",
            application.id,
            application.email,
        )
    except Exception:
        logger.exception(
            "Failed to send confirmation email for application_id=%s",
            application.id,
        )
