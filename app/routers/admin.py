from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.auth import clear_auth_cookie, create_access_token, set_auth_cookie, verify_admin_credentials
from app.cache import invalidate_jobs_cache
from app.config import get_settings
from app.dependencies import db_session_dependency, require_admin
from app.redis_client import get_redis

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


def _job_form_context(request: Request, job=None, error: str | None = None):
    return {"request": request, "job": job, "error": error}


@router.get("/login")
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": None},
    )


@router.post("/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not verify_admin_credentials(username, password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid admin credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_access_token(settings.admin_username)
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookie(response, token)
    return response


@router.post("/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookie(response)
    return response


@router.get("")
async def admin_dashboard(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    jobs = await crud.list_admin_jobs(session)
    applications = await crud.list_recent_applications(
        session, limit=settings.recent_applications_limit
    )
    return templates.TemplateResponse(
        "admin_dashboard.html",
        {"request": request, "jobs": jobs, "applications": applications},
    )


@router.get("/jobs/new")
async def admin_new_job(request: Request, _: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_job_form.html",
        _job_form_context(request),
    )


@router.post("/jobs")
async def admin_create_job(
    request: Request,
    title: str = Form(...),
    company: str = Form(...),
    location: str = Form(...),
    employment_type: str = Form(...),
    description: str = Form(...),
    requirements: str = Form(...),
    salary_range: str = Form(default=""),
    is_open: str | None = Form(default=None),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    try:
        payload = schemas.JobCreate(
            title=title,
            company=company,
            location=location,
            employment_type=employment_type,
            description=description,
            requirements=requirements,
            salary_range=salary_range or None,
            is_open=is_open is not None,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            "admin_job_form.html",
            _job_form_context(request, error=exc.errors()[0]["msg"]),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await crud.create_job(session, payload)
    await invalidate_jobs_cache(redis)
    return RedirectResponse(
        url="/admin",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/jobs/{job_id}/edit")
async def admin_edit_job_page(
    request: Request,
    job_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return templates.TemplateResponse(
        "admin_job_form.html",
        _job_form_context(request, job=job),
    )


@router.post("/jobs/{job_id}/edit")
async def admin_edit_job(
    request: Request,
    job_id: int,
    title: str = Form(...),
    company: str = Form(...),
    location: str = Form(...),
    employment_type: str = Form(...),
    description: str = Form(...),
    requirements: str = Form(...),
    salary_range: str = Form(default=""),
    is_open: str | None = Form(default=None),
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    try:
        payload = schemas.JobUpdate(
            title=title,
            company=company,
            location=location,
            employment_type=employment_type,
            description=description,
            requirements=requirements,
            salary_range=salary_range or None,
            is_open=is_open is not None,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            "admin_job_form.html",
            _job_form_context(request, job=job, error=exc.errors()[0]["msg"]),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    await crud.update_job(session, job, payload)
    await invalidate_jobs_cache(redis)
    return RedirectResponse(
        url=f"/admin/jobs/{job_id}/edit",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/jobs/{job_id}/toggle")
async def admin_toggle_job(
    job_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    await crud.toggle_job_open(session, job)
    await invalidate_jobs_cache(redis)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/{job_id}/delete")
async def admin_delete_job(
    job_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    await crud.delete_job(session, job)
    await invalidate_jobs_cache(redis)
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/applications")
async def admin_applications(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    applications = await crud.list_all_applications(session)
    return templates.TemplateResponse(
        "admin_applications.html",
        {"request": request, "applications": applications, "job": None},
    )


@router.get("/jobs/{job_id}/applications")
async def admin_job_applications(
    request: Request,
    job_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    job, applications = await crud.list_job_applications(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return templates.TemplateResponse(
        "admin_applications.html",
        {"request": request, "applications": applications, "job": job},
    )
