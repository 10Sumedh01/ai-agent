import re
from collections.abc import Iterator

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models import Base
from app.db.session import get_db, get_sessionmaker
from app.main import app
from app.services.llm import get_llm

TEST_DB_URL = settings.database_url.rsplit("/", 1)[0] + "/agent_test"


class FakeChatModel(BaseChatModel):
    """Deterministic stand-in for ChatGroq. Records every input it receives.
    Set `fail = True` to make the next call raise."""

    reply: str = "Hello from the fake model"
    fail: bool = False
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _check(self, messages: list[BaseMessage]) -> None:
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("fake LLM failure")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._check(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def _stream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        self._check(messages)
        # word + trailing whitespace, so "".join(chunks) == reply exactly
        for piece in re.findall(r"\S+\s*", self.reply):
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


@pytest.fixture
def fake_llm() -> FakeChatModel:
    return FakeChatModel()


@pytest_asyncio.fixture
async def fake_redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def client(fake_llm, fake_redis):
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_sessionmaker] = lambda: Session
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_redis] = lambda: fake_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
def make_user(client):
    async def _make(email="a@example.com", password="password123"):
        await client.post("/auth/register", json={"email": email, "password": password})
        r = await client.post("/auth/login", data={"username": email, "password": password})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _make    