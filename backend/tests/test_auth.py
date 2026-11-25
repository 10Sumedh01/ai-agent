async def test_register_and_me(client, make_user):
    headers = await make_user()
    r = await client.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"


async def test_duplicate_email_with_matching_password_logs_in(client):
    body = {"email": "a@example.com", "password": "password123"}
    assert (await client.post("/auth/register", json=body)).status_code == 201

    r = await client.post("/auth/register", json=body)
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"
    assert r.json()["access_token"]
    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"


async def test_duplicate_email_with_wrong_password_is_rejected(client):
    await client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "password123"},
    )
    r = await client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "different123"},
    )
    assert r.status_code == 401


async def test_wrong_password(client):
    await client.post("/auth/register", json={"email": "a@example.com", "password": "password123"})
    r = await client.post("/auth/login", data={"username": "a@example.com", "password": "wrongpass1"})
    assert r.status_code == 401


async def test_me_requires_token(client):
    assert (await client.get("/auth/me")).status_code == 401


async def test_short_password_rejected(client):
    r = await client.post("/auth/register", json={"email": "a@example.com", "password": "short"})
    assert r.status_code == 422
