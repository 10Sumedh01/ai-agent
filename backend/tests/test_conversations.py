async def test_crud(client, make_user):
    h = await make_user()

    r = await client.post("/conversations", json={"title": "First"}, headers=h)
    assert r.status_code == 201
    cid = r.json()["id"]

    r = await client.get("/conversations", headers=h)
    assert [c["id"] for c in r.json()] == [cid]

    r = await client.patch(f"/conversations/{cid}", json={"title": "Renamed"}, headers=h)
    assert r.json()["title"] == "Renamed"

    r = await client.get(f"/conversations/{cid}", headers=h)
    assert r.json()["messages"] == []

    assert (await client.delete(f"/conversations/{cid}", headers=h)).status_code == 204
    assert (await client.get(f"/conversations/{cid}", headers=h)).status_code == 404


async def test_user_isolation(client, make_user):
    h1 = await make_user("a@example.com")
    h2 = await make_user("b@example.com")

    cid = (await client.post("/conversations", json={}, headers=h1)).json()["id"]

    assert (await client.get(f"/conversations/{cid}", headers=h2)).status_code == 404
    assert (await client.patch(f"/conversations/{cid}", json={"title": "x"}, headers=h2)).status_code == 404
    assert (await client.delete(f"/conversations/{cid}", headers=h2)).status_code == 404
    assert (await client.get("/conversations", headers=h2)).json() == []


async def test_requires_auth(client):
    assert (await client.get("/conversations")).status_code == 401
