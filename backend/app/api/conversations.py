import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.models import Conversation, User
from app.db.session import get_db, get_sessionmaker
from app.schemas.chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from app.services.chat import DEFAULT_TITLE, add_message, load_history, make_title
from app.services.llm import get_llm, to_lc_messages

logger = logging.getLogger(__name__)
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


async def prepare_turn(
    conversation_id: uuid.UUID, content: str, db: AsyncSession, user: User
) -> list[BaseMessage]:
    """Ownership check, auto-title, persist the user message, return LLM input.
    Commits before the LLM call so no transaction is held open while waiting on Groq."""
    conv = await get_owned_conversation(conversation_id, db, user)
    if conv.title == DEFAULT_TITLE:
        conv.title = make_title(content)
    await add_message(db, conversation_id, "user", content)
    history = await load_history(db, conversation_id)
    return to_lc_messages(history)


def sse(event: str, data: dict) -> str:
    # JSON-encode so newlines inside tokens don't break SSE framing
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def save_partial(
    sessionmaker: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID, text: str
) -> None:
    if text.strip():
        async with sessionmaker() as s:
            await add_message(s, conversation_id, "assistant", text)


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


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    llm: BaseChatModel = Depends(get_llm),
):
    messages = await prepare_turn(conversation_id, data.content, db, user)
    try:
        reply = await llm.ainvoke(messages)
    except Exception as exc:  # narrowed to groq/httpx errors in Phase 5
        logger.exception("LLM call failed for conversation %s", conversation_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM request failed") from exc

    text = reply.content if isinstance(reply.content, str) else ""
    if not text.strip():
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM returned an empty response")
    return await add_message(db, conversation_id, "assistant", text)


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    llm: BaseChatModel = Depends(get_llm),
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
):
    # 404 / 422 are raised here, before any bytes are streamed
    messages = await prepare_turn(conversation_id, data.content, db, user)

    async def event_stream() -> AsyncIterator[str]:
        parts: list[str] = []
        try:
            async for chunk in llm.astream(messages):
                if isinstance(chunk.content, str) and chunk.content:
                    parts.append(chunk.content)
                    yield sse("token", {"text": chunk.content})

            text = "".join(parts)
            if not text.strip():
                yield sse("error", {"detail": "LLM returned an empty response"})
                return

            async with sessionmaker() as s:
                msg = await add_message(s, conversation_id, "assistant", text)
                done = {"id": str(msg.id), "created_at": msg.created_at.isoformat()}
            yield sse("done", done)

        except asyncio.CancelledError:
            # client disconnected: keep what we have; shield so the save isn't cancelled too
            await asyncio.shield(save_partial(sessionmaker, conversation_id, "".join(parts)))
            raise
        except Exception:
            logger.exception("LLM stream failed for conversation %s", conversation_id)
            await save_partial(sessionmaker, conversation_id, "".join(parts))
            yield sse("error", {"detail": "LLM request failed"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )