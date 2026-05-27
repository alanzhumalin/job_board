import logging
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Application, Job
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
        select(
            Job.id,
            Job.title,
            Job.company,
            Job.is_open,
            func.count(Application.id).label("applications_count"),
        )
        .outerjoin(Application, Application.job_id == Job.id)
        .group_by(Job.id)
        .order_by(desc(Job.created_at))
        .limit(limit)
    )
    return [
        {
            "id": row.id,
            "title": row.title,
            "company": row.company,
            "is_open": row.is_open,
            "applications_count": row.applications_count,
        }
        for row in result.all()
    ]


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


async def set_job_open_status(
    session: AsyncSession, job: Job, *, is_open: bool
) -> Job:
    job.is_open = is_open
    await session.commit()
    await session.refresh(job)
    logger.info("Set job id=%s is_open=%s", job.id, job.is_open)
    return job


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


async def delete_job(session: AsyncSession, job: Job) -> None:
    await session.delete(job)
    await session.commit()
    logger.info("Deleted job id=%s", job.id)


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
