import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", max_length=255)


class ConversationUpdate(BaseModel):
    title: str = Field(max_length=255)


class MessageCreate(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
    ]
    use_documents: bool = False
    # restrict retrieval to these documents; None = all of the user's documents
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


class SourceOut(BaseModel):
    index: int  # the [n] the model cites
    document_id: uuid.UUID
    filename: str
    page: int | None
    chunk_index: int
    score: float
    snippet: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    content: str
    sources: list[SourceOut] | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
