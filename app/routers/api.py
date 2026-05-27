from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.auth import create_access_token, set_auth_cookie, verify_admin_credentials
from app.cache import acquire_application_lock, get_cached_jobs_list, invalidate_jobs_cache, set_cached_jobs_list
from app.dependencies import db_session_dependency, require_admin
from app.email_service import send_application_confirmation
from app.redis_client import get_redis

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/jobs", response_model=list[schemas.JobRead])
async def api_list_jobs(
    page: int = 1,
    limit: int = 20,
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    cached_jobs = await get_cached_jobs_list(redis, page=page, limit=limit)
    if cached_jobs is not None:
        return cached_jobs

    jobs = await crud.list_open_jobs(session, page=page, limit=limit)
    serialized = [schemas.JobRead.model_validate(job).model_dump(mode="json") for job in jobs]
    await set_cached_jobs_list(redis, page=page, limit=limit, jobs=serialized)
    return serialized


@router.get("/jobs/{job_id}", response_model=schemas.JobRead)
async def api_get_job(
    job_id: int,
    session: AsyncSession = Depends(db_session_dependency),
):
    job = await crud.get_public_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/jobs/{job_id}/apply", response_model=schemas.ApplicationRead)
async def api_apply_to_job(
    job_id: int,
    payload: schemas.ApplicationCreate,
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_public_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or no longer open.")

    normalized_email = payload.email.lower()
    lock_acquired = await acquire_application_lock(
        redis, job_id=job_id, normalized_email=normalized_email
    )
    if not lock_acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A recent application attempt already exists for this email. Please try again later.",
        )

    try:
        application = await crud.create_application(session, job, payload)
    except crud.DuplicateApplicationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already applied to this job with this email address.",
        ) from exc

    await send_application_confirmation(application, job)
    return application


@router.post("/admin/login", response_model=schemas.TokenResponse)
async def api_admin_login(
    payload: schemas.AdminLoginRequest,
    response: Response,
):
    if not verify_admin_credentials(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials.",
        )

    token = create_access_token(payload.username)
    set_auth_cookie(response, token)
    return schemas.TokenResponse(access_token=token)


@router.get("/admin/applications", response_model=schemas.AdminApplicationsResponse)
async def api_admin_applications(
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    applications = await crud.list_all_applications(session)
    return schemas.AdminApplicationsResponse(applications=applications)


@router.get("/admin/jobs", response_model=list[schemas.JobRead])
async def api_admin_jobs(
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
):
    return await crud.list_admin_jobs(session)


@router.post("/admin/jobs", response_model=schemas.JobRead, status_code=status.HTTP_201_CREATED)
async def api_create_job(
    payload: schemas.JobCreate,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.create_job(session, payload)
    await invalidate_jobs_cache(redis)
    return job


@router.put("/admin/jobs/{job_id}", response_model=schemas.JobRead)
async def api_update_job(
    job_id: int,
    payload: schemas.JobUpdate,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(db_session_dependency),
    redis: Redis = Depends(get_redis),
):
    job = await crud.get_job_any_status(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    updated = await crud.update_job(session, job, payload)
    await invalidate_jobs_cache(redis)
    return updated


@router.delete("/admin/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_job(
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
