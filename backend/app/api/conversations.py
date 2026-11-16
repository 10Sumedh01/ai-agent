import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.models import Conversation, User
from app.db.session import get_db
from app.schemas.chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def get_owned_conversation(
    conversation_id: uuid.UUID, db: AsyncSession, user: User, with_messages: bool = False
) -> Conversation:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user.id
    )
    if with_messages:
        stmt = stmt.options(selectinload(Conversation.messages))
    conv = await db.scalar(stmt)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conv


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = Conversation(user_id=user.id, title=data.title)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
    )
    return result.all()


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await get_owned_conversation(conversation_id, db, user, with_messages=True)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await get_owned_conversation(conversation_id, db, user)
    conv.title = data.title
    await db.commit()
    await db.refresh(conv)
    return conv


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await get_owned_conversation(conversation_id, db, user)
    await db.delete(conv)
    await db.commit()
