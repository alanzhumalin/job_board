import json
import logging
import secrets
from datetime import UTC, datetime

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

BIND_TOKEN_TTL_SECONDS = 600
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
        and settings.telegram_bot_username
        and settings.app_base_url
    )


def _bind_key(token: str) -> str:
    return f"telegram:bind:{token}"


def _delete_key(telegram_user_id: int, job_id: int) -> str:
    return f"telegram:delete:{telegram_user_id}:{job_id}"


def _create_state_key(telegram_user_id: int) -> str:
    return f"telegram:create_job:{telegram_user_id}"


def _truncate(text: str | None, limit: int = 180) -> str:
    if not text:
        return "-"
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


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


async def send_message(chat_id: int, text: str) -> None:
    if not settings.telegram_bot_token:
        logger.warning("Telegram bot token is not configured. Skipping send_message.")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
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


async def create_connect_link(admin_username: str) -> str:
    token = secrets.token_urlsafe(24)
    payload = {
        "username": admin_username,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await redis_client.set(
        _bind_key(token),
        json.dumps(payload),
        ex=BIND_TOKEN_TTL_SECONDS,
    )
    return f"https://t.me/{settings.telegram_bot_username}?start=connect_{token}"


async def _consume_bind_token(token: str) -> dict | None:
    key = _bind_key(token)
    try:
        payload = await redis_client.get(key)
    except RedisError:
        logger.exception("Failed to read Telegram bind token")
        return None

    if not payload:
        return None

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = None

    try:
        await redis_client.delete(key)
    except RedisError:
        logger.exception("Failed to delete Telegram bind token")

    return data


async def _is_connected(telegram_user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        return await crud.is_telegram_admin(session, telegram_user_id)


def _whoami_text(user_id: int, username: str | None, connected: bool) -> str:
    return "\n".join(
        [
            f"Telegram user id: {user_id}",
            f"Username: {username or '-'}",
            f"Connected: {'yes' if connected else 'no'}",
        ]
    )


def _help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "/start",
            "/whoami",
            "/help",
            "/jobs",
            "/job <id>",
            "/open <id>",
            "/close <id>",
            "/delete <id>",
            "/confirm_delete <id>",
            "/applications",
            "/apps <job_id>",
            "/create",
        ]
    )


def _unauthorized_text() -> str:
    return (
        "Access denied. Open the admin dashboard and click Connect Telegram, "
        "or send /whoami to see your Telegram user ID."
    )


def _format_job_list_line(job: dict) -> str:
    status = "open" if job["is_open"] else "closed"
    return f"#{job['id']} - {job['title']} at {job['company']} ({status})"


def _format_job_detail(job: dict[str, object]) -> str:
    return "\n".join(
        [
            f"Job #{job['id']}",
            f"Title: {job['title']}",
            f"Company: {job['company']}",
            f"Location: {job['location']}",
            f"Employment type: {job['employment_type']}",
            f"Salary: {job['salary_range'] or '-'}",
            f"Status: {'Open' if job['is_open'] else 'Closed'}",
            f"Description: {_truncate(str(job['description']), 220)}",
            "",
            f"/open {job['id']}",
            f"/close {job['id']}",
            f"/apps {job['id']}",
            f"/delete {job['id']}",
        ]
    )


def _format_application_line(application: dict[str, object]) -> str:
    submitted = application["created_at"].strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"{application['full_name']} | {application['email']}",
        f"Job: {application.get('job_title') or 'Unknown job'}",
        f"Submitted: {submitted}",
    ]
    if application.get("phone"):
        parts.insert(2, f"Phone: {application['phone']}")
    if application.get("cover_letter"):
        parts.append(f"Cover letter: {_truncate(application['cover_letter'], 120)}")
    return "\n".join(parts)


async def _get_create_state(telegram_user_id: int) -> dict | None:
    try:
        payload = await redis_client.get(_create_state_key(telegram_user_id))
    except RedisError:
        logger.exception("Failed to read Telegram create flow state for user_id=%s", telegram_user_id)
        return None

    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


async def _set_create_state(telegram_user_id: int, state: dict) -> bool:
    try:
        await redis_client.set(
            _create_state_key(telegram_user_id),
            json.dumps(state),
            ex=CREATE_FLOW_TTL_SECONDS,
        )
        return True
    except RedisError:
        logger.exception("Failed to write Telegram create flow state for user_id=%s", telegram_user_id)
        return False


async def _clear_create_state(telegram_user_id: int) -> None:
    try:
        await redis_client.delete(_create_state_key(telegram_user_id))
    except RedisError:
        logger.exception("Failed to clear Telegram create flow state for user_id=%s", telegram_user_id)


async def _set_delete_confirmation(telegram_user_id: int, job_id: int) -> bool:
    try:
        await redis_client.set(
            _delete_key(telegram_user_id, job_id),
            "1",
            ex=DELETE_CONFIRM_TTL_SECONDS,
        )
        return True
    except RedisError:
        logger.exception(
            "Failed to store Telegram delete confirmation for telegram_user_id=%s job_id=%s",
            telegram_user_id,
            job_id,
        )
        return False


async def _has_delete_confirmation(telegram_user_id: int, job_id: int) -> bool:
    try:
        return bool(await redis_client.get(_delete_key(telegram_user_id, job_id)))
    except RedisError:
        logger.exception(
            "Failed to read Telegram delete confirmation for telegram_user_id=%s job_id=%s",
            telegram_user_id,
            job_id,
        )
        return False


async def _clear_delete_confirmation(telegram_user_id: int, job_id: int) -> None:
    try:
        await redis_client.delete(_delete_key(telegram_user_id, job_id))
    except RedisError:
        logger.exception(
            "Failed to clear Telegram delete confirmation for telegram_user_id=%s job_id=%s",
            telegram_user_id,
            job_id,
        )


async def _handle_create_conversation(chat_id: int, telegram_user_id: int, text: str, state: dict) -> bool:
    normalized = text.strip()
    if normalized.lower() == "cancel":
        await _clear_create_state(telegram_user_id)
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
            await _clear_create_state(telegram_user_id)
            await send_message(chat_id, f"Could not create job: {exc.errors()[0]['msg']}")
            return True

        async with AsyncSessionLocal() as session:
            job = await crud.create_job_from_bot(session, payload)
        await invalidate_jobs_cache(redis_client)
        await _clear_create_state(telegram_user_id)
        await send_message(chat_id, f"Created job #{job.id}: {job.title}")
        return True

    step_index = state.get("step_index", 0)
    if step_index >= len(CREATE_FLOW_FIELDS):
        await _clear_create_state(telegram_user_id)
        await send_message(chat_id, "This create flow expired. Send /create to start again.")
        return True

    field_name, _prompt = CREATE_FLOW_FIELDS[step_index]
    value = "" if field_name == "salary_range" and normalized.lower() == "skip" else normalized
    data[field_name] = value

    next_index = step_index + 1
    if next_index >= len(CREATE_FLOW_FIELDS):
        next_state = {"data": data, "awaiting_confirmation": True}
        stored = await _set_create_state(telegram_user_id, next_state)
        if not stored:
            await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again.")
            return True
        summary = "\n".join(
            [
                "Create this job?",
                f"Title: {data.get('title', '-')}",
                f"Company: {data.get('company', '-')}",
                f"Location: {data.get('location', '-')}",
                f"Employment type: {data.get('employment_type', '-')}",
                f"Salary: {data.get('salary_range') or '-'}",
                f"Description: {_truncate(data.get('description'), 160)}",
                f"Requirements: {_truncate(data.get('requirements'), 160)}",
                "",
                "Type yes to create or cancel to abort.",
            ]
        )
        await send_message(chat_id, summary)
        return True

    next_state = {"step_index": next_index, "data": data}
    stored = await _set_create_state(telegram_user_id, next_state)
    if not stored:
        await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again.")
        return True
    await send_message(chat_id, CREATE_FLOW_FIELDS[next_index][1])
    return True


async def _handle_authorized_command(chat_id: int, telegram_user_id: int, username: str | None, text: str) -> None:
    state = await _get_create_state(telegram_user_id)
    if state:
        handled = await _handle_create_conversation(chat_id, telegram_user_id, text, state)
        if handled:
            return

    command, argument = _parse_command(text)
    if command == "/start":
        await send_message(chat_id, "Telegram connected successfully. You can now manage jobs from this bot.\n\n" + _help_text())
        return
    if command == "/whoami":
        connected = await _is_connected(telegram_user_id)
        await send_message(chat_id, _whoami_text(telegram_user_id, username, connected))
        return
    if command == "/help":
        await send_message(chat_id, _help_text())
        return
    if command == "/jobs":
        async with AsyncSessionLocal() as session:
            jobs = await crud.list_jobs_for_bot(session, limit=10)
        if not jobs:
            await send_message(chat_id, "No jobs found.")
            return
        await send_message(chat_id, "Recent jobs:\n\n" + "\n".join(_format_job_list_line(job) for job in jobs))
        return
    if command == "/job":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /job <id>")
            return
        async with AsyncSessionLocal() as session:
            job = await crud.get_job_for_bot(session, job_id)
        if not job:
            await send_message(chat_id, "Job not found.")
            return
        await send_message(chat_id, _format_job_detail(job))
        return
    if command in {"/open", "/close"}:
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, f"Usage: {command} <id>")
            return
        async with AsyncSessionLocal() as session:
            job = await crud.get_job_any_status(session, job_id)
            if not job:
                await send_message(chat_id, "Job not found.")
                return
            await crud.set_job_open_status(session, job, is_open=command == "/open")
        await invalidate_jobs_cache(redis_client)
        await send_message(chat_id, f"Job #{job_id} marked as {'open' if command == '/open' else 'closed'}.")
        return
    if command == "/delete":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /delete <id>")
            return
        async with AsyncSessionLocal() as session:
            job = await crud.get_job_any_status(session, job_id)
        if not job:
            await send_message(chat_id, "Job not found.")
            return
        stored = await _set_delete_confirmation(telegram_user_id, job_id)
        if not stored:
            await send_message(chat_id, "Delete confirmation is temporarily unavailable. Please try again.")
            return
        await send_message(chat_id, f"Type /confirm_delete {job_id} to delete this job.")
        return
    if command == "/confirm_delete":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /confirm_delete <id>")
            return
        if not await _has_delete_confirmation(telegram_user_id, job_id):
            await send_message(chat_id, "Delete confirmation not found or expired. Start again with /delete <id>.")
            return
        async with AsyncSessionLocal() as session:
            deleted = await crud.delete_job_by_id(session, job_id)
        await _clear_delete_confirmation(telegram_user_id, job_id)
        if not deleted:
            await send_message(chat_id, "Job not found.")
            return
        await invalidate_jobs_cache(redis_client)
        await send_message(chat_id, f"Deleted job #{job_id}.")
        return
    if command == "/applications":
        async with AsyncSessionLocal() as session:
            applications = await crud.list_latest_applications_for_bot(
                session, limit=settings.recent_applications_limit
            )
        if not applications:
            await send_message(chat_id, "No recent applications found.")
            return
        await send_message(
            chat_id,
            "Latest applications:\n\n" + "\n\n".join(_format_application_line(application) for application in applications),
        )
        return
    if command == "/apps":
        job_id = _parse_job_id(argument)
        if job_id is None:
            await send_message(chat_id, "Usage: /apps <job_id>")
            return
        async with AsyncSessionLocal() as session:
            job, applications = await crud.list_applications_for_job_for_bot(session, job_id)
        if not job:
            await send_message(chat_id, "Job not found.")
            return
        if not applications:
            await send_message(chat_id, f"No applications found for {job['title']}.")
            return
        await send_message(
            chat_id,
            f"Applications for {job['title']}:\n\n" + "\n\n".join(_format_application_line(application) for application in applications[:10]),
        )
        return
    if command == "/create":
        stored = await _set_create_state(telegram_user_id, {"step_index": 0, "data": {}})
        if not stored:
            await send_message(chat_id, "Interactive create flow is temporarily unavailable. Please try again.")
            return
        await send_message(chat_id, "Starting job creation. Type cancel at any step to abort.\n\nSend the job title.")
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
        telegram_user_id = from_user.get("id")
        chat_id = chat.get("id")
        username = from_user.get("username")
        first_name = from_user.get("first_name")

        if not telegram_user_id or not chat_id:
            return

        command, argument = _parse_command(text)

        if command == "/whoami":
            connected = await _is_connected(telegram_user_id)
            await send_message(chat_id, _whoami_text(telegram_user_id, username, connected))
            return

        if command == "/start" and argument.startswith("connect_"):
            token = argument.removeprefix("connect_")
            bind_data = await _consume_bind_token(token)
            if not bind_data:
                await send_message(
                    chat_id,
                    "This connection link is invalid or expired. Please open the admin dashboard and click Connect Telegram again.",
                )
                return

            async with AsyncSessionLocal() as session:
                await crud.get_or_create_telegram_admin(
                    session,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    first_name=first_name,
                )
            await send_message(
                chat_id,
                "Telegram connected successfully. You can now manage jobs from this bot. Send /help.",
            )
            return

        connected = await _is_connected(telegram_user_id)
        if not connected:
            if command == "/start":
                await send_message(chat_id, _unauthorized_text())
                return
            await send_message(chat_id, _unauthorized_text())
            return

        await _handle_authorized_command(chat_id, telegram_user_id, username, text)
    except Exception:
        logger.exception("Telegram update processing failed")
        try:
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id:
                await send_message(chat_id, "Something went wrong. Please try again.")
        except Exception:
            logger.exception("Failed to send Telegram error message")
