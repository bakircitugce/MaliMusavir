from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.security import (
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.auth.service import validate_password_strength
from app.core.config import Settings
from app.core.models import User
from tests.auth_helpers import TEST_PASSWORD, make_actor, make_buro


# --- Şifre özetleme ---------------------------------------------------------


def test_password_hash_format_and_verification() -> None:
    hashed = hash_password("GucluSifre2026!")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("GucluSifre2026!", hashed)
    assert not verify_password("yanlis", hashed)
    assert not verify_password("", hashed)
    assert not verify_password("x", None)
    assert not verify_password("x", "bozuk-format")


def test_legacy_hash_still_verifies_and_is_flagged_for_rehash() -> None:
    import hashlib

    legacy = "abcd:" + hashlib.pbkdf2_hmac("sha256", b"eski", b"abcd", 100_000).hex()
    assert verify_password("eski", legacy)
    assert needs_rehash(legacy)
    assert not needs_rehash(hash_password("yeni"))


def test_production_settings_reject_weak_iterations_and_short_secret(monkeypatch) -> None:
    monkeypatch.delenv("PASSWORD_HASH_ITERATIONS", raising=False)
    with pytest.raises(ValueError):
        Settings(auth_secret="k" * 40, app_env="production", password_hash_iterations=100_000)
    with pytest.raises(ValueError):
        Settings(auth_secret="kisa", app_env="production")
    assert Settings(auth_secret="k" * 40, app_env="production").password_hash_iterations >= 600_000


def test_password_policy() -> None:
    validate_password_strength("GucluSifre2026")
    for weak in ["kisa1A", "tamamenkucuk2026", "TAMAMENBUYUK2026", "HarfOlupRakamYok"]:
        with pytest.raises(ValueError):
            validate_password_strength(weak)


# --- Token ------------------------------------------------------------------


def _token(**overrides) -> str:
    args = {"user_id": str(uuid4()), "buro_id": str(uuid4()), "rol": "admin", "token_version": 0}
    args.update(overrides)
    return create_token(**args)


def test_token_contains_required_claims() -> None:
    payload = decode_token(_token(rol="musavir", token_version=3))
    assert payload is not None
    assert payload["rol"] == "musavir"
    assert payload["tv"] == 3
    assert {"exp", "iat", "sub", "buro_id"} <= payload.keys()


def test_tampered_expired_and_foreign_tokens_are_rejected() -> None:
    token = _token()
    assert decode_token(token[:-4] + "abcd") is None
    assert decode_token(_token(expires_in=timedelta(seconds=-1))) is None
    foreign = jwt.encode(
        {"sub": "x", "buro_id": "y", "tv": 0, "iat": 0, "exp": 9_999_999_999},
        "baska-bir-anahtar-" + "z" * 32,
        algorithm="HS256",
    )
    assert decode_token(foreign) is None
    unsigned = jwt.encode(
        {"sub": "x", "buro_id": "y", "tv": 0, "iat": 0, "exp": 9_999_999_999},
        key=None,
        algorithm="none",
    )
    assert decode_token(unsigned) is None
    assert decode_token("") is None


# --- Giriş ------------------------------------------------------------------


async def test_no_default_accounts_exist(api_client: AsyncClient) -> None:
    for email, password in [
        ("admin@maliasistan.com", "admin123"),
        ("muhasebe@maliasistan.com", "user123"),
        ("admin", "admin123"),
    ]:
        res = await api_client.post("/auth/login", json={"email": email, "password": password})
        assert res.status_code == 401


async def test_login_me_and_role_normalization(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    actor = await make_actor(db_factory, buro_id, rol="yonetici", email="eski@test.local")

    res = await api_client.post(
        "/auth/login", json={"email": "ESKI@test.local ", "password": TEST_PASSWORD}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["rol"] == "admin"
    assert decode_token(body["token"])["rol"] == "admin"

    me = await api_client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(actor.user.id)


async def test_unknown_user_and_wrong_password_return_same_message(
    api_client: AsyncClient, db_factory
) -> None:
    buro_id = await make_buro(db_factory)
    await make_actor(db_factory, buro_id, email="var@test.local")

    unknown = await api_client.post(
        "/auth/login", json={"email": "yok@test.local", "password": TEST_PASSWORD}
    )
    wrong = await api_client.post(
        "/auth/login", json={"email": "var@test.local", "password": "Yanlis2026xx"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


async def test_account_locks_after_repeated_failures(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    await make_actor(db_factory, buro_id, email="kilit@test.local")

    for _ in range(5):
        res = await api_client.post(
            "/auth/login", json={"email": "kilit@test.local", "password": "Yanlis2026xx"}
        )
        assert res.status_code == 401

    # Doğru şifre bile kilit süresince reddedilir (ve aynı mesajı alır).
    locked = await api_client.post(
        "/auth/login", json={"email": "kilit@test.local", "password": TEST_PASSWORD}
    )
    assert locked.status_code == 401

    async with db_factory() as session:
        user = await session.scalar(select(User).where(User.email == "kilit@test.local"))
        assert user.locked_until is not None


async def test_login_rate_limit(api_client: AsyncClient) -> None:
    statuses = []
    for _ in range(12):
        res = await api_client.post(
            "/auth/login", json={"email": "biri@test.local", "password": "Yanlis2026xx"}
        )
        statuses.append(res.status_code)
    assert statuses[:10] == [401] * 10
    assert statuses[10:] == [429, 429]


async def test_logout_revokes_existing_tokens(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    actor = await make_actor(db_factory, buro_id)

    assert (await api_client.get("/auth/me", headers=actor.headers())).status_code == 200
    assert (await api_client.post("/auth/logout", headers=actor.headers())).status_code == 200
    assert (await api_client.get("/auth/me", headers=actor.headers())).status_code == 401


async def test_deactivated_user_token_is_rejected(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    admin = await make_actor(db_factory, buro_id, rol="admin")
    staff = await make_actor(db_factory, buro_id, rol="personel")

    res = await api_client.patch(
        f"/auth/users/{staff.user.id}/status?is_active=false", headers=admin.headers()
    )
    assert res.status_code == 200
    assert (await api_client.get("/auth/me", headers=staff.headers())).status_code == 401


# --- Kullanıcı yönetimi -----------------------------------------------------


async def test_user_management_and_password_change(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    admin = await make_actor(db_factory, buro_id, rol="admin")
    staff = await make_actor(db_factory, buro_id, rol="personel")

    assert (await api_client.get("/auth/users", headers=staff.headers())).status_code == 403
    listed = await api_client.get("/auth/users", headers=admin.headers())
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    weak = await api_client.post(
        "/auth/users",
        headers=admin.headers(),
        json={"email": "a@test.local", "ad_soyad": "Ahmet", "password": "admin123"},
    )
    assert weak.status_code == 400

    created = await api_client.post(
        "/auth/users",
        headers=admin.headers(),
        json={
            "email": "ahmet@test.local",
            "ad_soyad": "Ahmet Yılmaz",
            "password": "AhmetSifre2026",
            "rol": "musavir",
        },
    )
    assert created.status_code == 201
    new_id = created.json()["id"]

    login = await api_client.post(
        "/auth/login", json={"email": "ahmet@test.local", "password": "AhmetSifre2026"}
    )
    assert login.status_code == 200
    old_token = login.json()["token"]

    changed = await api_client.post(
        f"/auth/users/{new_id}/password",
        headers=admin.headers(),
        json={"new_password": "YeniSifre2026x"},
    )
    assert changed.status_code == 200

    # Eski token ve eski şifre geçersiz, yeni şifre çalışır.
    me = await api_client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert me.status_code == 401
    old = await api_client.post(
        "/auth/login", json={"email": "ahmet@test.local", "password": "AhmetSifre2026"}
    )
    assert old.status_code == 401
    new = await api_client.post(
        "/auth/login", json={"email": "ahmet@test.local", "password": "YeniSifre2026x"}
    )
    assert new.status_code == 200


async def test_staff_cannot_change_other_users_password(
    api_client: AsyncClient, db_factory
) -> None:
    buro_id = await make_buro(db_factory)
    admin = await make_actor(db_factory, buro_id, rol="admin")
    staff = await make_actor(db_factory, buro_id, rol="personel")

    res = await api_client.post(
        f"/auth/users/{admin.user.id}/password",
        headers=staff.headers(),
        json={"new_password": "Ele2026Gecirme"},
    )
    assert res.status_code == 404
    own = await api_client.post(
        f"/auth/users/{staff.user.id}/password",
        headers=staff.headers(),
        json={"new_password": "KendiSifrem2026"},
    )
    assert own.status_code == 200


async def test_admin_cannot_manage_users_of_another_buro(
    api_client: AsyncClient, db_factory
) -> None:
    buro_a = await make_buro(db_factory)
    buro_b = await make_buro(db_factory)
    admin_a = await make_actor(db_factory, buro_a, rol="admin")
    victim = await make_actor(db_factory, buro_b, rol="admin")

    listed = await api_client.get("/auth/users", headers=admin_a.headers())
    assert str(victim.user.id) not in {u["id"] for u in listed.json()}

    pw = await api_client.post(
        f"/auth/users/{victim.user.id}/password",
        headers=admin_a.headers(),
        json={"new_password": "Ele2026Gecirme"},
    )
    st = await api_client.patch(
        f"/auth/users/{victim.user.id}/status?is_active=false", headers=admin_a.headers()
    )
    asg = await api_client.get(
        f"/auth/users/{victim.user.id}/mukellefler", headers=admin_a.headers()
    )
    assert pw.status_code == st.status_code == asg.status_code == 404
    assert (await api_client.get("/auth/me", headers=victim.headers())).status_code == 200


async def test_mukellef_assignment_flow(api_client: AsyncClient, db_factory) -> None:
    m1, m2, foreign = uuid4(), uuid4(), uuid4()
    buro_id = await make_buro(db_factory, mukellef_ids=[m1, m2])
    await make_buro(db_factory, mukellef_ids=[foreign])
    admin = await make_actor(db_factory, buro_id, rol="admin")
    staff = await make_actor(db_factory, buro_id, rol="personel")

    mine = await api_client.get("/auth/me/mukellefler", headers=staff.headers())
    assert mine.json() == []
    assert len((await api_client.get("/auth/me/mukellefler", headers=admin.headers())).json()) == 2

    put = await api_client.put(
        f"/auth/users/{staff.user.id}/mukellefler/{m1}", headers=admin.headers()
    )
    assert put.status_code == 204
    cross = await api_client.put(
        f"/auth/users/{staff.user.id}/mukellefler/{foreign}", headers=admin.headers()
    )
    assert cross.status_code == 404

    mine = await api_client.get("/auth/me/mukellefler", headers=staff.headers())
    assert [m["id"] for m in mine.json()] == [str(m1)]

    deleted = await api_client.delete(
        f"/auth/users/{staff.user.id}/mukellefler/{m1}", headers=admin.headers()
    )
    assert deleted.status_code == 204
    assert (await api_client.get("/auth/me/mukellefler", headers=staff.headers())).json() == []
