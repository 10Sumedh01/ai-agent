from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.rate_limit import rate_limit_login, rate_limit_register
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut | Token,
    dependencies=[Depends(rate_limit_register)],
)
async def register(
    data: UserCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = data.email.lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        if not verify_password(data.password, existing.hashed_password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
        return Token(access_token=create_access_token(str(existing.id)))

    user = User(email=email, hashed_password=hash_password(data.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(User).where(User.email == email))
        if existing is None:
            raise
        if not verify_password(data.password, existing.hashed_password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
        return Token(access_token=create_access_token(str(existing.id)))
    await db.refresh(user)
    response.status_code = status.HTTP_201_CREATED
    return user


@router.post(
    "/login",
    response_model=Token,
    dependencies=[Depends(rate_limit_login)],
)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == form.username.lower()))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout")
async def logout():
    return {
        "message": (
            "Discard the access token on the client. This endpoint does not "
            "revoke issued tokens."
        )
    }
