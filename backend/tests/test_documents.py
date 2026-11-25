import uuid

from app.api import conversations as conversations_api
from app.api import documents as documents_api
from app.services.documents import document_path


async def test_upload_and_list_document(client, make_user, monkeypatch, tmp_path):
    headers = await make_user()
    monkeypatch.setattr(documents_api, "index_document", lambda *args: 2)
    monkeypatch.setattr(documents_api.settings, "document_storage_path", tmp_path)

    response = await client.post(
        "/documents",
        headers=headers,
        files={"file": ("notes.txt", b"Useful project notes", "text/plain")},
    )

    assert response.status_code == 201
    document = response.json()
    assert document["filename"] == "notes.txt"
    assert document["status"] == "ready"
    assert document["chunk_count"] == 2
    assert document["size_bytes"] == len(b"Useful project notes")
    stored_path = document_path(uuid.UUID(document["id"]), "notes.txt")
    assert stored_path.read_text() == "Useful project notes"

    listed = await client.get("/documents", headers=headers)
    assert [item["id"] for item in listed.json()] == [document["id"]]


async def test_upload_rejects_unsupported_file(client, make_user):
    headers = await make_user()

    response = await client.post(
        "/documents",
        headers=headers,
        files={"file": ("image.png", b"not a document", "image/png")},
    )

    assert response.status_code == 422


async def test_documents_are_private_and_delete_removes_file(
    client, make_user, monkeypatch, tmp_path
):
    owner = await make_user("owner@example.com")
    other_user = await make_user("other@example.com")
    monkeypatch.setattr(documents_api, "index_document", lambda *args: 1)
    monkeypatch.setattr(documents_api, "delete_document_vectors", lambda *args: None)
    monkeypatch.setattr(documents_api.settings, "document_storage_path", tmp_path)

    uploaded = await client.post(
        "/documents",
        headers=owner,
        files={"file": ("private.txt", b"Private notes", "text/plain")},
    )
    document_id = uploaded.json()["id"]
    path = document_path(uuid.UUID(document_id), "private.txt")
    assert path.exists()

    assert (await client.get("/documents", headers=other_user)).json() == []
    deleted = await client.delete(f"/documents/{document_id}", headers=other_user)
    assert deleted.status_code == 404
    assert path.exists()

    deleted = await client.delete(f"/documents/{document_id}", headers=owner)
    assert deleted.status_code == 204
    assert not path.exists()
    assert (await client.get("/documents", headers=owner)).json() == []


async def test_chat_uses_owned_document_context(
    client, make_user, fake_llm, monkeypatch, tmp_path
):
    headers = await make_user()
    monkeypatch.setattr(documents_api, "index_document", lambda *args: 1)
    monkeypatch.setattr(documents_api.settings, "document_storage_path", tmp_path)
    source = {
        "document_id": "doc-id",
        "filename": "guide.txt",
        "page": 1,
        "chunk_index": 0,
        "score": 0.9,
        "text": "Use dependency injection to replace external services in tests.",
    }
    retrieved_for = []

    def retrieve(query, user_id):
        retrieved_for.append(user_id)
        return [source]

    monkeypatch.setattr(conversations_api, "search_documents", retrieve)
    await client.post(
        "/documents",
        headers=headers,
        files={"file": ("guide.txt", b"Some guide text", "text/plain")},
    )
    conversation = await client.post("/conversations", headers=headers, json={})

    response = await client.post(
        f"/conversations/{conversation.json()['id']}/messages",
        headers=headers,
        json={"content": "How do I test services?"},
    )

    assert response.status_code == 201
    assert len(retrieved_for) == 1
    assert "[1] guide.txt (page 1)" in fake_llm.calls[-1][0].content
    assert source["text"] in fake_llm.calls[-1][0].content
