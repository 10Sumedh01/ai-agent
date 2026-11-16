async def test_register_and_me(client, make_user):
    headers = await make_user()
    r = await client.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"


async def test_duplicate_email(client):
    body = {"email": "a@example.com", "password": "password123"}
    assert (await client.post("/auth/register", json=body)).status_code == 201
    assert (await client.post("/auth/register", json=body)).status_code == 409


async def test_wrong_password(client):
    await client.post("/auth/register", json={"email": "a@example.com", "password": "password123"})
    r = await client.post("/auth/login", data={"username": "a@example.com", "password": "wrongpass1"})
    assert r.status_code == 401


async def test_me_requires_token(client):
    assert (await client.get("/auth/me")).status_code == 401


async def test_short_password_rejected(client):
    r = await client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert r.status_code == 422
