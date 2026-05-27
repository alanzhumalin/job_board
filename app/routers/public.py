from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.cache import acquire_application_lock, get_cached_jobs_list, set_cached_jobs_list
from app.dependencies import db_session_dependency
from app.email_service import send_application_confirmation
from app.redis_client import get_redis

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def render_error(template_name: str, request: Request, **context):
    return templates.TemplateResponse(template_name, {"request": request, **context})


@router.get("/")
async def home(
    request: Request,
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    cached_jobs = await get_cached_jobs_list(redis, page=1, limit=20)
    jobs = cached_jobs
    if jobs is None:
        db_jobs = await crud.list_open_jobs(session, page=1, limit=20)
        jobs = [schemas.JobRead.model_validate(job).model_dump(mode="json") for job in db_jobs]
        await set_cached_jobs_list(redis, page=1, limit=20, jobs=jobs)

    return templates.TemplateResponse(
        "public_jobs.html",
        {"request": request, "jobs": jobs, "page_title": "Open Roles"},
    )


@router.get("/jobs")
async def jobs_list(
    request: Request,
    page: int = 1,
    limit: int = 20,
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    cached_jobs = await get_cached_jobs_list(redis, page=page, limit=limit)
    jobs = cached_jobs
    if jobs is None:
        db_jobs = await crud.list_open_jobs(session, page=page, limit=limit)
        jobs = [schemas.JobRead.model_validate(job).model_dump(mode="json") for job in db_jobs]
        await set_cached_jobs_list(redis, page=page, limit=limit, jobs=jobs)

    return templates.TemplateResponse(
        "public_jobs.html",
        {"request": request, "jobs": jobs, "page_title": "All Jobs"},
    )


@router.get("/jobs/{job_id}")
async def job_detail(
    request: Request,
    job_id: int,
    session: AsyncSession = Depends(db_session_dependency),
):
    job = await crud.get_public_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "error": None,
            "form_data": {},
        },
    )


@router.post("/jobs/{job_id}/apply")
async def apply_to_job(
    request: Request,
    job_id: int,
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(default=""),
    cover_letter: str = Form(default=""),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_public_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or no longer open.")

    form_data = {
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "cover_letter": cover_letter,
    }

    try:
        payload = schemas.ApplicationCreate(
            full_name=full_name,
            email=email,
            phone=phone or None,
            cover_letter=cover_letter or None,
        )
    except ValidationError as exc:
        return render_error(
            "job_detail.html",
            request,
            job=job,
            error=exc.errors()[0]["msg"],
            form_data=form_data,
        )

    normalized_email = payload.email.lower()
    lock_acquired = await acquire_application_lock(
        redis, job_id=job_id, normalized_email=normalized_email
    )
    if not lock_acquired:
        return render_error(
            "job_detail.html",
            request,
            job=job,
            error="A recent application attempt already exists for this email. Please wait a few minutes before trying again.",
            form_data=form_data,
        )

    try:
        application = await crud.create_application(session, job, payload)
    except crud.DuplicateApplicationError:
        return render_error(
            "job_detail.html",
            request,
            job=job,
            error="You have already applied to this role with this email address.",
            form_data=form_data,
        )

    await send_application_confirmation(application, job)
    return RedirectResponse(
        url=f"/jobs/{job_id}/success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/jobs/{job_id}/success")
async def application_success(
    request: Request,
    job_id: int,
    session: AsyncSession = Depends(db_session_dependency),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return templates.TemplateResponse(
        "apply_success.html",
        {"request": request, "job": job},
    )
