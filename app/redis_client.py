import logging

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> Redis:
    return redis_client
