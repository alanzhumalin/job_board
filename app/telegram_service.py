import json
import logging
from datetime import UTC

import httpx
from pydantic import ValidationError
from redis.exceptions import RedisError

from app import crud, schemas
from app.cache import invalidate_jobs_cache
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.redis_client import redis_client

logger = logging.getLogger(__name__)
settings = get_settings()

CREATE_FLOW_TTL_SECONDS = 1800
DELETE_CONFIRM_TTL_SECONDS = 300
CREATE_FLOW_FIELDS = [
    ("title", "Send the job title."),
    ("company", "Send the company name."),
    ("location", "Send the location."),
    (
        "employment_type",
        "Send the employment type. Suggested values: Full-time, Part-time, Contract, Internship, Temporary, Freelance, Remote, Hybrid, On-site.",
    ),
    ("salary_range", "Send the salary range, or type skip."),
    ("description", "Send the job description."),
    ("requirements", "Send the job requirements."),
]


def telegram_is_configured() -> bool:
    return bool(
        settings.telegram_bot_token
        and settings.telegram_webhook_secret
        and settings.app_base_url
    )


def _allowed_admin_ids() -> set[int]:
    raw = settings.telegram_admin_ids or ""
    allowed: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            allowed.add(int(value))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ADMIN_IDS entry: %s", value)
    return allowed


def _is_authorized(user_id: int) -> bool:
    return user_id in _allowed_admin_ids()


def _whoami_text(user_id: int, username: str | None) -> str:
    username_line = username or "-"
    return f"Telegram user id: {user_id}\nUsername: {username_line}"


def _access_denied_text(user_id: int, username: str | None) -> str:
    return (
        "Access denied. Send /whoami to get your Telegram user ID.\n\n"
        f"{_whoami_text(user_id, username)}"
    )


def _create_state_key(user_id: int) -> str:
    return f"telegram:create_job:{user_id}"


def _delete_confirm_key(user_id: int) -> str:
    return f"telegram:delete_job:{user_id}"


def _truncate(text: str | None, length: int = 180) -> str:
    if not text:
        return "-"
    value = " ".join(text.split())
    if len(value) <= length:
        return value
    return f"{value[: length - 3]}..."


async def send_message(chat_id: int, text: str) -> None:
    if not settings.telegram_bot_token:
        logger.warning("Telegram bot token is not configured. Skipping send_message.")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def setup_webhook() -> None:
    if not telegram_is_configured():
        logger.info("Telegram webhook setup skipped because Telegram settings are incomplete.")
        return

    webhook_url = (
        f"{settings.app_base_url.rstrip('/')}/telegram/webhook/"
        f"{settings.telegram_webhook_secret}"
    )
    api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(api_url, json={"url": webhook_url})
            response.raise_for_status()
        logger.info("Telegram webhook configured for %s", webhook_url)
    except Exception:
        logger.exception("Failed to configure Telegram webhook for %s", webhook_url)


async def _get_create_state(user_id: int) -> dict | None:
    try:
        payload = await redis_client.get(_create_state_key(user_id))
    except RedisError:
        logger.exception("Failed to read Telegram create flow state for user_id=%s", user_id)
        return None

    if not payload:
        return None

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Invalid Telegram create flow state for user_id=%s", user_id)
        return None


async def _set_create_state(user_id: int, state: dict) -> bool:
    try:
        await redis_client.set(
            _create_state_key(user_id),
            json.dumps(state),
            ex=CREATE_FLOW_TTL_SECONDS,
        )
        return True
    except RedisError:
        logger.exception("Failed to store Telegram create flow state for user_id=%s", user_id)
        return False


async def _clear_create_state(user_id: int) -> None:
    try:
        await redis_client.delete(_create_state_key(user_id))
    except RedisError:
        logger.exception("Failed to clear Telegram create flow state for user_id=%s", user_id)


async def _set_delete_confirmation(user_id: int, job_id: int) -> bool:
    try:
        await redis_client.set(
            _delete_confirm_key(user_id),
            str(job_id),
            ex=DELETE_CONFIRM_TTL_SECONDS,
        )
        return True
    except RedisError:
        logger.exception("Failed to store Telegram delete confirmation for user_id=%s", user_id)
        return False


async def _get_delete_confirmation(user_id: int) -> int | None:
    try:
        payload = await redis_client.get(_delete_confirm_key(user_id))
    except RedisError:
        logger.exception("Failed to read Telegram delete confirmation for user_id=%s", user_id)
        return None

    if not payload:
        return None

    try:
        return int(payload)
    except ValueError:
        return None


async def _clear_delete_confirmation(user_id: int) -> None:
    try:
        await redis_client.delete(_delete_confirm_key(user_id))
    except RedisError:
        logger.exception("Failed to clear Telegram delete confirmation for user_id=%s", user_id)


def _help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "/start - Welcome message",
            "/whoami - Show your Telegram user ID",
            "/help - Show this help",
            "/jobs - List recent jobs",
            "/job <id> - Show one job",
            "/open <id> - Open a job",
            "/close <id> - Close a job",
            "/delete <id> - Request job deletion",
            "/confirm_delete <id> - Confirm deletion",
            "/apps <job_id> - Show applications for a job",
            "/applications - Show latest applications",
            "/create - Start a new job creation flow",
        ]
    )


def _parse_command(text: str) -> tuple[str, str]:
    normalized = text.strip()
    if not normalized:
        return "", ""

    parts = normalized.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


def _parse_job_id(argument: str) -> int | None:
    try:
        return int(argument.strip())
    except (TypeError, ValueError):
        return None


def _format_job_summary(job: dict) -> str:
    status = "open" if job["is_open"] else "closed"
    applications_count = job.get("applications_count")
    applications_label = (
        f" | applications: {applications_count}"
        if applications_count is not None
        else ""
    )
    return (
        f"#{job['id']} - {job['title']} at {job['company']} | "
        f"{status}{applications_label}"
    )


def _format_job_detail(job: dict[str, object]) -> str:
    status = "Open" if job["is_open"] else "Closed"
    salary = job["salary_range"] or "-"
    preview = _truncate(str(job["description"]), 220)
    return "\n".join(
        [
            f"Job #{job['id']}",
            f"Title: {job['title']}",
            f"Company: {job['company']}",
            f"Location: {job['location']}",
            f"Employment type: {job['employment_type']}",
            f"Salary: {salary}",
            f"Status: {status}",
            f"Description: {preview}",
            "",
            f"/close {job['id']}",
            f"/open {job['id']}",
            f"/delete {job['id']}",
            f"/apps {job['id']}",
        ]
    )


def _format_application_line(application: dict[str, object]) -> str:
    submitted = application["created_at"].strftime("%Y-%m-%d %H:%M UTC")
    phone = application["phone"] or "-"
    cover_letter = _truncate(application["cover_letter"], 120)
    job_title = application.get("job_title") or "Unknown job"
    return "\n".join(
        [
            f"{application['full_name']} | {application['email']}",
            f"Phone: {phone}",
            f"Job: {job_title}",
            f"Submitted: {submitted}",
            f"Cover letter: {cover_letter}",
        ]
    )


async def _handle_jobs(chat_id: int) -> None:
    async with AsyncSessionLocal() as session:
        jobs = await crud.list_jobs_for_bot(session, limit=10)

    if not jobs:
        await send_message(chat_id, "No jobs found.")
        return

    text = "Recent jobs:\n\n" + "\n".join(_format_job_summary(job) for job in jobs)
    await send_message(chat_id, text)


async def _handle_job_detail(chat_id: int, job_id: int) -> None:
    async with AsyncSessionLocal() as session:
        job = await crud.get_job_any_status(session, job_id)

    if not job:
        await send_message(chat_id, "Job not found.")
        return

    await send_message(chat_id, _format_job_detail(crud.job_to_view(job)))


async def _handle_job_status(chat_id: int, job_id: int, *, is_open: bool) -> None:
    async with AsyncSessionLocal() as session:
        job = await crud.get_job_any_status(session, job_id)
        if not job:
            await send_message(chat_id, "Job not found.")
            return
        await crud.set_job_open_status(session, job, is_open=is_open)

    await invalidate_jobs_cache(redis_client)
    status_text = "open" if is_open else "closed"
    await send_message(chat_id, f"Job #{job_id} marked as {status_text}.")


async def _handle_delete_request(chat_id: int, user_id: int, job_id: int) -> None:
    async with AsyncSessionLocal() as session:
        job = await crud.get_job_any_status(session, job_id)

    if not job:
        await send_message(chat_id, "Job not found.")
        return

    stored = await _set_delete_confirmation(user_id, job_id)
    if not stored:
        await send_message(chat_id, "Delete confirmation is temporarily unavailable. Please try again.")
        return

    await send_message(
        chat_id,
        f"Type /confirm_delete {job_id} to confirm deleting {job.title}.",
    )


async def _handle_delete_confirm(chat_id: int, user_id: int, job_id: int) -> None:
    pending_job_id = await _get_delete_confirmation(user_id)
    if pending_job_id != job_id:
        await send_message(chat_id, "No matching delete confirmation found. Start again with /delete <id>.")
        return

    async with AsyncSessionLocal() as session:
        job = await crud.get_job_any_status(session, job_id)
        if not job:
            await _clear_delete_confirmation(user_id)
            await send_message(chat_id, "Job not found.")
            return
        await crud.delete_job(session, job)

    await _clear_delete_confirmation(user_id)
    await invalidate_jobs_cache(redis_client)
    await send_message(chat_id, f"Deleted job #{job_id}.")


async def _handle_job_applications(chat_id: int, job_id: int) -> None:
    async with AsyncSessionLocal() as session:
        job, applications = await crud.list_job_applications(session, job_id)

    if not job:
        await send_message(chat_id, "Job not found.")
        return

    if not applications:
        await send_message(chat_id, f"No applications found for {job.title}.")
        return

    lines = [f"Applications for {job.title}:"]
    for application in applications[:10]:
        lines.append("")
        lines.append(_format_application_line(crud.application_to_view(application)))
    await send_message(chat_id, "\n".join(lines))


async def _handle_latest_applications(chat_id: int) -> None:
    async with AsyncSessionLocal() as session:
        applications = await crud.list_recent_applications(
            session, limit=settings.recent_applications_limit
        )

    if not applications:
        await send_message(chat_id, "No recent applications found.")
        return

    lines = ["Latest applications:"]
    for application in applications:
        lines.append("")
        lines.append(_format_application_line(crud.application_to_view(application)))
    await send_message(chat_id, "\n".join(lines))


async def _start_create_flow(chat_id: int, user_id: int) -> None:
    state = {"step_index": 0, "data": {}}
    stored = await _set_create_state(user_id, state)
    if not stored:
        await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again later.")
        return
    await send_message(chat_id, "Starting job creation.\nType cancel at any step to abort.\n\nSend the job title.")


def _build_create_summary(data: dict[str, str]) -> str:
    salary_range = data.get("salary_range") or "-"
    return "\n".join(
        [
            "Create this job?",
            f"Title: {data.get('title', '-')}",
            f"Company: {data.get('company', '-')}",
            f"Location: {data.get('location', '-')}",
            f"Employment type: {data.get('employment_type', '-')}",
            f"Salary: {salary_range}",
            f"Description: {_truncate(data.get('description'), 160)}",
            f"Requirements: {_truncate(data.get('requirements'), 160)}",
            "",
            "Type yes to create or cancel to abort.",
        ]
    )


async def _handle_create_flow_message(
    chat_id: int, user_id: int, text: str, state: dict
) -> bool:
    normalized = text.strip()
    if normalized.lower() == "cancel":
        await _clear_create_state(user_id)
        await send_message(chat_id, "Cancelled.")
        return True

    data = state.get("data", {})

    if state.get("awaiting_confirmation"):
        if normalized.lower() != "yes":
            await send_message(chat_id, "Type yes to create or cancel to abort.")
            return True

        try:
            payload = schemas.JobCreate(
                title=data["title"],
                company=data["company"],
                location=data["location"],
                employment_type=data["employment_type"],
                salary_range=data.get("salary_range") or None,
                description=data["description"],
                requirements=data["requirements"],
                is_open=True,
            )
        except ValidationError as exc:
            await _clear_create_state(user_id)
            await send_message(chat_id, f"Could not create job: {exc.errors()[0]['msg']}")
            return True

        async with AsyncSessionLocal() as session:
            job = await crud.create_job(session, payload)
        await invalidate_jobs_cache(redis_client)
        await _clear_create_state(user_id)
        await send_message(chat_id, f"Created job #{job.id}: {job.title}")
        return True

    step_index = state.get("step_index", 0)
    if step_index >= len(CREATE_FLOW_FIELDS):
        await _clear_create_state(user_id)
        await send_message(chat_id, "The create flow expired. Send /create to start again.")
        return True

    field_name, _prompt = CREATE_FLOW_FIELDS[step_index]
    value = normalized
    if field_name == "salary_range" and value.lower() == "skip":
        value = ""
    data[field_name] = value

    next_index = step_index + 1
    if next_index >= len(CREATE_FLOW_FIELDS):
        next_state = {"data": data, "awaiting_confirmation": True}
        stored = await _set_create_state(user_id, next_state)
        if not stored:
            await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again later.")
            return True
        await send_message(chat_id, _build_create_summary(data))
        return True

    next_state = {"step_index": next_index, "data": data}
    stored = await _set_create_state(user_id, next_state)
    if not stored:
        await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again later.")
        return True
    await send_message(chat_id, CREATE_FLOW_FIELDS[next_index][1])
    return True


async def _handle_authorized_message(
    chat_id: int, user_id: int, username: str | None, text: str
) -> None:
    state = await _get_create_state(user_id)
    if state:
        handled = await _handle_create_flow_message(chat_id, user_id, text, state)
        if handled:
            return

    command, argument = _parse_command(text)

    if command == "/start":
        await send_message(
            chat_id,
            "Welcome to the Job Board admin bot.\n\n" + _help_text(),
        )
        return

    if command == "/whoami":
        await send_message(chat_id, _whoami_text(user_id, username))
        return

    if command == "/help":
        await send_message(chat_id, _help_text())
        return

    if command == "/jobs":
        await _handle_jobs(chat_id)
        return

    if command == "/job":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /job <id>")
            return
        await _handle_job_detail(chat_id, job_id)
        return

    if command == "/open":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /open <id>")
            return
        await _handle_job_status(chat_id, job_id, is_open=True)
        return

    if command == "/close":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /close <id>")
            return
        await _handle_job_status(chat_id, job_id, is_open=False)
        return

    if command == "/delete":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /delete <id>")
            return
        await _handle_delete_request(chat_id, user_id, job_id)
        return

    if command == "/confirm_delete":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /confirm_delete <id>")
            return
        await _handle_delete_confirm(chat_id, user_id, job_id)
        return

    if command == "/apps":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /apps <job_id>")
            return
        await _handle_job_applications(chat_id, job_id)
        return

    if command == "/applications":
        await _handle_latest_applications(chat_id)
        return

    if command == "/create":
        await _start_create_flow(chat_id, user_id)
        return

    await send_message(chat_id, "Unknown command. Send /help to see available commands.")


async def process_update(update: dict) -> None:
    try:
        message = update.get("message") or {}
        text = message.get("text")
        if not text:
            return

        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = from_user.get("id")
        chat_id = chat.get("id")
        username = from_user.get("username")

        if not user_id or not chat_id:
            return

        command, _argument = _parse_command(text)
        if command == "/whoami":
            await send_message(chat_id, _whoami_text(user_id, username))
            return

        if not _is_authorized(user_id):
            if command == "/start":
                await send_message(chat_id, _access_denied_text(user_id, username))
                return
            await send_message(
                chat_id,
                "Access denied. Send /whoami to get your Telegram user ID.",
            )
            return

        await _handle_authorized_message(chat_id, user_id, username, text)
    except Exception:
        logger.exception("Telegram update processing failed")
        try:
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id:
                await send_message(chat_id, "Something went wrong. Please try again.")
        except Exception:
            logger.exception("Failed to send Telegram error message")
