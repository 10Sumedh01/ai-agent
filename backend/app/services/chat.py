import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message

HISTORY_LIMIT = 20
DEFAULT_TITLE = "New chat"
TITLE_MAX = 60


def make_title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= TITLE_MAX else t[: TITLE_MAX - 1].rstrip() + "…"


async def add_message(
    db: AsyncSession, conversation_id: uuid.UUID, role: str, content: str
) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def load_history(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int = HISTORY_LIMIT
) -> list[tuple[str, str]]:
    """Last `limit` messages, oldest first, as (role, content)."""
    rows = (
        await db.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [(r.role, r.content) for r in reversed(rows)]