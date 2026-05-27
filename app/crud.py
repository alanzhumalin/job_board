import logging
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Application, Job, TelegramAdmin
from app.schemas import ApplicationCreate, JobCreate, JobUpdate

logger = logging.getLogger(__name__)


class DuplicateApplicationError(Exception):
    pass


def job_to_view(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "employment_type": job.employment_type,
        "description": job.description,
        "requirements": job.requirements,
        "salary_range": job.salary_range,
        "is_open": job.is_open,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def application_to_view(application: Application) -> dict[str, Any]:
    job = application.job
    return {
        "id": application.id,
        "job_id": application.job_id,
        "full_name": application.full_name,
        "email": application.email,
        "phone": application.phone,
        "cover_letter": application.cover_letter,
        "created_at": application.created_at,
        "job_title": job.title if job else None,
        "job_company": job.company if job else None,
    }


async def count_jobs(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Job.id)))
    return result.scalar_one()


async def create_sample_job(session: AsyncSession) -> Job:
    job = Job(
        title="Backend Engineer",
        company="Acme Jobs",
        location="Remote",
        employment_type="Full-time",
        description=(
            "Build internal hiring tools, public job experiences, and pragmatic APIs."
        ),
        requirements=(
            "Experience with Python, SQL, APIs, and shipping maintainable products."
        ),
        salary_range="$80k - $110k",
        is_open=True,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    logger.info("Seeded sample job with id=%s", job.id)
    return job


async def list_open_jobs(
    session: AsyncSession, *, page: int = 1, limit: int = 20
) -> list[Job]:
    offset = (page - 1) * limit
    result = await session.execute(
        select(Job)
        .where(Job.is_open.is_(True))
        .order_by(desc(Job.created_at))
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_public_job(session: AsyncSession, job_id: int) -> Job | None:
    result = await session.execute(
        select(Job).where(Job.id == job_id, Job.is_open.is_(True))
    )
    return result.scalar_one_or_none()


async def get_job_any_status(session: AsyncSession, job_id: int) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def list_admin_jobs(session: AsyncSession) -> list[Job]:
    result = await session.execute(select(Job).order_by(desc(Job.created_at)))
    return list(result.scalars().all())


async def list_jobs_for_bot(
    session: AsyncSession, *, limit: int = 10
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Job).order_by(desc(Job.created_at)).limit(limit)
    )
    jobs = list(result.scalars().all())
    return [job_to_view(job) for job in jobs]


async def get_or_create_telegram_admin(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    username: str | None,
    first_name: str | None,
) -> TelegramAdmin:
    result = await session.execute(
        select(TelegramAdmin).where(TelegramAdmin.telegram_user_id == telegram_user_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.username = username
        existing.first_name = first_name
        await session.commit()
        await session.refresh(existing)
        logger.info("Updated Telegram admin link for telegram_user_id=%s", telegram_user_id)
        return existing

    admin = TelegramAdmin(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    logger.info("Created Telegram admin link for telegram_user_id=%s", telegram_user_id)
    return admin


async def is_telegram_admin(session: AsyncSession, telegram_user_id: int) -> bool:
    result = await session.execute(
        select(TelegramAdmin.id).where(TelegramAdmin.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none() is not None


async def list_recent_applications(
    session: AsyncSession, *, limit: int = 10
) -> list[Application]:
    result = await session.execute(
        select(Application)
        .options(selectinload(Application.job))
        .order_by(desc(Application.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_all_applications(session: AsyncSession) -> list[Application]:
    result = await session.execute(
        select(Application)
        .options(selectinload(Application.job))
        .order_by(desc(Application.created_at))
    )
    return list(result.scalars().all())


async def list_job_applications(
    session: AsyncSession, job_id: int
) -> tuple[Job | None, list[Application]]:
    job = await get_job_any_status(session, job_id)
    if not job:
        return None, []

    result = await session.execute(
        select(Application)
        .where(Application.job_id == job_id)
        .options(selectinload(Application.job))
        .order_by(desc(Application.created_at))
    )
    return job, list(result.scalars().all())


async def get_job_for_bot(session: AsyncSession, job_id: int) -> dict[str, Any] | None:
    job = await get_job_any_status(session, job_id)
    if not job:
        return None
    return job_to_view(job)


async def list_latest_applications_for_bot(
    session: AsyncSession, *, limit: int = 10
) -> list[dict[str, Any]]:
    applications = await list_recent_applications(session, limit=limit)
    return [application_to_view(application) for application in applications]


async def list_applications_for_job_for_bot(
    session: AsyncSession, job_id: int
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    job, applications = await list_job_applications(session, job_id)
    if not job:
        return None, []
    return job_to_view(job), [application_to_view(application) for application in applications]


async def create_job(session: AsyncSession, payload: JobCreate) -> Job:
    job = Job(**payload.model_dump())
    session.add(job)
    await session.commit()
    await session.refresh(job)
    logger.info("Created job id=%s", job.id)
    return job


async def update_job(session: AsyncSession, job: Job, payload: JobUpdate) -> Job:
    for key, value in payload.model_dump().items():
        setattr(job, key, value)
    await session.commit()
    await session.refresh(job)
    logger.info("Updated job id=%s", job.id)
    return job


async def toggle_job_open(session: AsyncSession, job: Job) -> Job:
    job.is_open = not job.is_open
    await session.commit()
    await session.refresh(job)
    logger.info("Toggled job id=%s is_open=%s", job.id, job.is_open)
    return job


async def set_job_open_status(
    session: AsyncSession, job: Job, *, is_open: bool
) -> Job:
    job.is_open = is_open
    await session.commit()
    await session.refresh(job)
    logger.info("Set job id=%s is_open=%s", job.id, job.is_open)
    return job


async def delete_job(session: AsyncSession, job: Job) -> None:
    await session.delete(job)
    await session.commit()
    logger.info("Deleted job id=%s", job.id)


async def delete_job_by_id(session: AsyncSession, job_id: int) -> bool:
    job = await get_job_any_status(session, job_id)
    if not job:
        return False
    await delete_job(session, job)
    return True


async def create_job_from_bot(session: AsyncSession, payload: JobCreate) -> Job:
    return await create_job(session, payload)


async def create_application(
    session: AsyncSession, job: Job, payload: ApplicationCreate
) -> Application:
    normalized_email = payload.email.lower()
    application = Application(
        job_id=job.id,
        full_name=payload.full_name.strip(),
        email=normalized_email,
        phone=payload.phone.strip() if payload.phone else None,
        cover_letter=payload.cover_letter.strip() if payload.cover_letter else None,
    )
    session.add(application)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateApplicationError from exc

    await session.refresh(application)
    logger.info("Created application id=%s job_id=%s", application.id, job.id)
    return application
