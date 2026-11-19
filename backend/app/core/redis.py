from redis.asyncio import Redis

from app.core.config import settings

redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Redis:
    """Dependency so tests can swap in fakeredis."""
    return redis_client