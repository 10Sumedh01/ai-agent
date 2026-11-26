import logging
import time

from fastapi import Depends, HTTPException, Request, status
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


async def _rate_limit_auth(
    action: str,
    limit: int,
    request: Request,
    redis: Redis,
) -> None:
    now = int(time.time())
    client_ip = request.client.host if request.client else "unknown"
    key = f"rl:auth:{action}:{client_ip}:{now // WINDOW_SECONDS}"
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS)
            count, _ = await pipe.execute()
    except RedisError:
        logger.warning(
            "Authentication rate limiter unavailable, allowing request",
            exc_info=True,
        )
        return

    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many authentication attempts",
            headers={"Retry-After": str(WINDOW_SECONDS - now % WINDOW_SECONDS)},
        )


async def rate_limit_login(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> None:
    await _rate_limit_auth(
        "login",
        settings.auth_login_rate_limit_per_minute,
        request,
        redis,
    )


async def rate_limit_register(
    request: Request,
    redis: Redis = Depends(get_redis),
) -> None:
    await _rate_limit_auth(
        "register",
        settings.auth_register_rate_limit_per_minute,
        request,
        redis,
    )