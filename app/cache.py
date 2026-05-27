import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

CACHE_PATTERN = "v1:jobs:list:*"


def jobs_list_cache_key(page: int, limit: int) -> str:
    return f"v1:jobs:list:open=true:sort=newest:page={page}:limit={limit}"


def application_lock_key(job_id: int, normalized_email: str) -> str:
    return f"v1:apply-lock:job={job_id}:email={normalized_email}"


async def get_cached_jobs_list(
    redis: Redis, *, page: int, limit: int
) -> list[dict] | None:
    key = jobs_list_cache_key(page, limit)
    try:
        payload = await redis.get(key)
        if not payload:
            return None
        logger.info("Jobs list cache hit for key=%s", key)
        return json.loads(payload)
    except RedisError:
        logger.exception("Redis cache read failed for key=%s", key)
        return None


async def set_cached_jobs_list(
    redis: Redis, *, page: int, limit: int, jobs: list[dict]
) -> None:
    key = jobs_list_cache_key(page, limit)
    try:
        await redis.set(key, json.dumps(jobs), ex=settings.jobs_cache_ttl_seconds)
        logger.info("Jobs list cache populated for key=%s", key)
    except RedisError:
        logger.exception("Redis cache write failed for key=%s", key)


async def invalidate_jobs_cache(redis: Redis) -> None:
    try:
        cursor = 0
        deleted = 0

        while True:
            cursor, keys = await redis.scan(
                cursor=cursor, match=CACHE_PATTERN, count=100
            )
            if keys:
                deleted += await redis.delete(*keys)
            if cursor == 0 or cursor == "0":
                break

        logger.info("Invalidated %s job list cache keys", deleted)
    except RedisError:
        logger.exception("Redis cache invalidation failed for pattern=%s", CACHE_PATTERN)


async def acquire_application_lock(
    redis: Redis, *, job_id: int, normalized_email: str
) -> bool:
    key = application_lock_key(job_id, normalized_email)
    try:
        acquired = await redis.set(
            key,
            "1",
            ex=settings.apply_lock_ttl_seconds,
            nx=True,
        )
        return bool(acquired)
    except RedisError:
        logger.exception(
            "Redis application lock failed for job_id=%s email=%s. Continuing with database uniqueness fallback.",
            job_id,
            normalized_email,
        )
        return True
