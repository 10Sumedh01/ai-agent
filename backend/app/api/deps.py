import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = uuid.UUID(decode_access_token(token)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user