import json
import uuid
from types import SimpleNamespace

from app.api import rate_limit
from app.core.config import settings
from app.services.llm import SYSTEM_PROMPT


async def new_conversation(client, headers, **body) -> str:
    r = await client.post("/conversations", json=body, headers=headers)
    assert r.status_code == 201
    return r.json()["id"]


async def get_messages(client, headers, cid) -> list[dict]:
    r = await client.get(f"/conversations/{cid}", headers=headers)
    assert r.status_code == 200
    return sorted(r.json()["messages"], key=lambda m: m["created_at"])


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


# ---------- non-streaming ----------


async def test_send_message_returns_reply_and_persists(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)

    r = await client.post(f"/conversations/{cid}/messages", json={"content": "Hi there"}, headers=h)

    assert r.status_code == 201
    assert r.json()["role"] == "assistant"
    assert r.json()["content"] == fake_llm.reply

    msgs = await get_messages(client, h, cid)
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "Hi there"),
        ("assistant", fake_llm.reply),
    ]
    conv = (await client.get(f"/conversations/{cid}", headers=h)).json()
    assert conv["title"] == "Hi there"


async def test_history_is_sent_to_llm(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)

    await client.post(f"/conversations/{cid}/messages", json={"content": "first"}, headers=h)
    await client.post(f"/conversations/{cid}/messages", json={"content": "second"}, headers=h)

    assert [(m.type, m.content) for m in fake_llm.calls[-1]] == [
        ("system", SYSTEM_PROMPT),
        ("human", "first"),
        ("ai", fake_llm.reply),
        ("human", "second"),
    ]


async def test_custom_title_is_not_overwritten(client, make_user):
    h = await make_user()
    cid = await new_conversation(client, h, title="My title")

    await client.post(f"/conversations/{cid}/messages", json={"content": "hello"}, headers=h)

    conv = (await client.get(f"/conversations/{cid}", headers=h)).json()
    assert conv["title"] == "My title"


async def test_other_users_conversation_is_404(client, make_user, fake_llm):
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    cid = await new_conversation(client, alice)

    for path in ("messages", "messages/stream"):
        r = await client.post(f"/conversations/{cid}/{path}", json={"content": "hi"}, headers=bob)
        assert r.status_code == 404
    assert fake_llm.calls == []


async def test_unknown_conversation_is_404(client, make_user):
    h = await make_user()
    r = await client.post(
        f"/conversations/{uuid.uuid4()}/messages", json={"content": "hi"}, headers=h
    )
    assert r.status_code == 404


async def test_blank_content_is_422(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)

    r = await client.post(f"/conversations/{cid}/messages", json={"content": "   "}, headers=h)

    assert r.status_code == 422
    assert fake_llm.calls == []


async def test_requires_auth(client, make_user):
    h = await make_user()
    cid = await new_conversation(client, h)
    r = await client.post(f"/conversations/{cid}/messages", json={"content": "hi"})
    assert r.status_code == 401


async def test_llm_failure_is_502_and_keeps_user_message(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)
    fake_llm.fail = True

    r = await client.post(f"/conversations/{cid}/messages", json={"content": "hi"}, headers=h)

    assert r.status_code == 502
    msgs = await get_messages(client, h, cid)
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "hi")]


# ---------- streaming ----------


async def test_stream_emits_tokens_then_done_and_persists(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)

    r = await client.post(
        f"/conversations/{cid}/messages/stream", json={"content": "stream please"}, headers=h
    )

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    tokens = [d["text"] for e, d in events if e == "token"]
    assert len(tokens) > 1
    assert "".join(tokens) == fake_llm.reply
    assert events[-1][0] == "done"

    msgs = await get_messages(client, h, cid)
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == fake_llm.reply
    assert msgs[-1]["id"] == events[-1][1]["id"]


async def test_stream_llm_failure_emits_error_event(client, make_user, fake_llm):
    h = await make_user()
    cid = await new_conversation(client, h)
    fake_llm.fail = True

    r = await client.post(f"/conversations/{cid}/messages/stream", json={"content": "hi"}, headers=h)

    assert r.status_code == 200  # headers were sent before the failure
    assert parse_sse(r.text) == [("error", {"detail": "LLM request failed"})]
    msgs = await get_messages(client, h, cid)
    assert [m["role"] for m in msgs] == ["user"]


# ---------- rate limit ----------


async def test_rate_limit_returns_429_per_user(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 2)
    # freeze the clock 20s into a window so the test can't straddle a minute boundary
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: 6_000_020.0))

    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    a_cid = await new_conversation(client, alice)
    b_cid = await new_conversation(client, bob)

    codes = []
    for path in ("messages", "messages/stream", "messages"):  # both endpoints share one budget
        r = await client.post(f"/conversations/{a_cid}/{path}", json={"content": "hi"}, headers=alice)
        codes.append(r.status_code)

    assert codes == [201, 200, 429]
    assert r.headers["retry-after"] == "40"

    r = await client.post(f"/conversations/{b_cid}/messages", json={"content": "hi"}, headers=bob)
    assert r.status_code == 201  # bob has his own budget
    