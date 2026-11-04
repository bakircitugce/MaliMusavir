import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import create_token, decode_token, hash_password, verify_password
from app.auth.service import ensure_default_accounts
from app.core.db import get_session
from app.core.models import Base
from app.main import app


@pytest.fixture
async def auth_test_session():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        await ensure_default_accounts(session)
        yield session

    await test_engine.dispose()


@pytest.fixture
async def auth_client(auth_test_session: AsyncSession):
    async def override_get_session():
        yield auth_test_session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def test_password_hashing():
    pwd = "secretPassword123"
    h = hash_password(pwd)
    assert h != pwd
    assert ":" in h
    assert verify_password(pwd, h) is True
    assert verify_password("wrongPass", h) is False
    assert verify_password("", h) is False
    assert verify_password(pwd, None) is False


def test_token_creation_and_decoding():
    token = create_token("user-123", "test@test.com", "yonetici", "buro-456")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["email"] == "test@test.com"
    assert payload["rol"] == "yonetici"

    # Tampered token
    tampered = token[:-4] + "abcd"
    assert decode_token(tampered) is None


@pytest.mark.asyncio
async def test_admin_and_user_login(auth_client: AsyncClient):
    # 1. Admin login with correct credentials
    res = await auth_client.post(
        "/auth/login",
        json={"email": "admin@maliasistan.com", "password": "admin123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["user"]["rol"] == "yonetici"
    admin_token = data["token"]

    # 2. Login with wrong password
    bad_res = await auth_client.post(
        "/auth/login",
        json={"email": "admin@maliasistan.com", "password": "wrong_password"},
    )
    assert bad_res.status_code == 401

    # 3. Standard user login
    user_res = await auth_client.post(
        "/auth/login",
        json={"email": "muhasebe@maliasistan.com", "password": "user123"},
    )
    assert user_res.status_code == 200
    user_data = user_res.json()
    assert user_data["user"]["rol"] == "kullanici"
    user_token = user_data["token"]

    # 4. Check /auth/me for both
    me_admin = await auth_client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert me_admin.status_code == 200
    assert me_admin.json()["rol"] == "yonetici"

    me_user = await auth_client.get("/auth/me", headers={"Authorization": f"Bearer {user_token}"})
    assert me_user.status_code == 200
    assert me_user.json()["rol"] == "kullanici"


@pytest.mark.asyncio
async def test_user_management_and_password_setting(auth_client: AsyncClient):
    # Admin login
    res = await auth_client.post(
        "/auth/login",
        json={"email": "admin@maliasistan.com", "password": "admin123"},
    )
    admin_token = res.json()["token"]

    # Normal user login
    res_u = await auth_client.post(
        "/auth/login",
        json={"email": "muhasebe@maliasistan.com", "password": "user123"},
    )
    user_token = res_u.json()["token"]

    # User attempts to list users -> 403 Forbidden
    forbidden_res = await auth_client.get("/auth/users", headers={"Authorization": f"Bearer {user_token}"})
    assert forbidden_res.status_code == 403

    # Admin lists users -> 200 OK
    users_res = await auth_client.get("/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert users_res.status_code == 200
    users = users_res.json()
    assert len(users) >= 2

    # Admin creates a new user
    new_user_res = await auth_client.post(
        "/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "ahmet@maliasistan.com",
            "ad_soyad": "Ahmet Yılmaz",
            "password": "ahmetPassword2026",
            "rol": "kullanici",
        },
    )
    assert new_user_res.status_code == 201
    new_user = new_user_res.json()
    new_user_id = new_user["id"]

    # New user logs in
    login_new = await auth_client.post(
        "/auth/login",
        json={"email": "ahmet@maliasistan.com", "password": "ahmetPassword2026"},
    )
    assert login_new.status_code == 200

    # Admin sets/changes password for the new user
    change_res = await auth_client.post(
        f"/auth/users/{new_user_id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_password": "newSuperPassword99"},
    )
    assert change_res.status_code == 200

    # Old password fails
    fail_login = await auth_client.post(
        "/auth/login",
        json={"email": "ahmet@maliasistan.com", "password": "ahmetPassword2026"},
    )
    assert fail_login.status_code == 401

    # New password succeeds
    success_login = await auth_client.post(
        "/auth/login",
        json={"email": "ahmet@maliasistan.com", "password": "newSuperPassword99"},
    )
    assert success_login.status_code == 200
