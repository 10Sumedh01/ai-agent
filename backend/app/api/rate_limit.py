import logging
import time

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.redis import get_redis
from app.db.models import User

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


async def rate_limit_chat(
    user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
) -> None:
    now = int(time.time())
    key = f"rl:chat:{user.id}:{now // WINDOW_SECONDS}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS)
            count, _ = await pipe.execute()
    except RedisError:
        logger.warning("Rate limiter unavailable, allowing request", exc_info=True)
        return

    if count > settings.chat_rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded",
            headers={"Retry-After": str(WINDOW_SECONDS - now % WINDOW_SECONDS)},
        )
        