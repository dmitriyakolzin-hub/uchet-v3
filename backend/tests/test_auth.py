"""Приёмка этапа 0: войти с поддельным токеном невозможно."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import create_token
from tests.conftest import TEST_PASSWORD


def _login(client, login="ivanov", password=TEST_PASSWORD):
    return client.post("/api/auth/login", json={"login": login, "password": password})


def _me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def _claims(user, **overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id),
        "type": "access",
        "ver": user.token_version,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _b64(data: dict) -> str:
    raw = json.dumps(data, default=lambda d: int(d.timestamp())).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# --- Нормальный вход ---


def test_login_and_me(client, make_user):
    make_user(full_name="Иванов И.И.")
    resp = _login(client)
    assert resp.status_code == 200
    tokens = resp.json()

    me = _me(client, tokens["access_token"])
    assert me.status_code == 200
    assert me.json()["login"] == "ivanov"
    assert me.json()["full_name"] == "Иванов И.И."
    assert "password_hash" not in me.json()


def test_login_is_case_insensitive(client, make_user):
    make_user()
    assert _login(client, login="  IVANOV ").status_code == 200


def test_wrong_password(client, make_user):
    make_user()
    resp = _login(client, password="wrong-password")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Неверный логин или пароль"


def test_unknown_user_same_answer(client):
    resp = _login(client, login="nobody")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Неверный логин или пароль"


def test_inactive_user_cannot_login(client, make_user):
    make_user(is_active=False)
    assert _login(client).status_code == 401


def test_refresh_gives_new_working_tokens(client, make_user):
    make_user()
    refresh_token = _login(client).json()["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert _me(client, resp.json()["access_token"]).status_code == 200


# --- Поддельные и негодные токены ---


def test_no_token(client):
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Basic abc", "abc.def.ghi"])
def test_malformed_header(client, header):
    assert client.get("/api/auth/me", headers={"Authorization": header}).status_code == 401


@pytest.mark.parametrize("token", ["garbage", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30."])
def test_garbage_token(client, token):
    assert _me(client, token).status_code == 401


def test_token_signed_with_other_secret(client, make_user):
    user = make_user()
    forged = jwt.encode(_claims(user), "attacker-secret-" + "y" * 40, algorithm="HS256")
    assert _me(client, forged).status_code == 401


def test_token_signed_with_old_system_default_secret(client, make_user):
    """Типичная атака на старую систему: секрет по умолчанию из исходников."""
    user = make_user(is_admin=True)
    for guess in ["your-secret-key-change-in-production", "secret", "change-me"]:
        forged = jwt.encode(_claims(user), guess, algorithm="HS256")
        assert _me(client, forged).status_code == 401


def test_alg_none_rejected(client, make_user):
    user = make_user(is_admin=True)
    forged = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims(user))}."
    assert _me(client, forged).status_code == 401


def test_other_algorithm_rejected(client, make_user):
    """Токен с нашим секретом, но другим алгоритмом (HS512) — тоже отказ."""
    user = make_user()
    token = jwt.encode(_claims(user), get_settings().JWT_SECRET, algorithm="HS512")
    assert _me(client, token).status_code == 401


def test_tampered_payload_rejected(client, make_user):
    """Подменили id пользователя в настоящем токене — подпись не сходится."""
    make_user()
    admin = make_user(login="admin", is_admin=True)
    header, _, signature = _login(client).json()["access_token"].split(".")
    tampered = f"{header}.{_b64(_claims(admin))}.{signature}"
    assert _me(client, tampered).status_code == 401


def test_expired_token(client, make_user):
    user = make_user()
    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        _claims(user, iat=past - timedelta(minutes=5), exp=past),
        get_settings().JWT_SECRET,
        algorithm="HS256",
    )
    assert _me(client, token).status_code == 401


@pytest.mark.parametrize("missing", ["exp", "sub", "type", "ver", "iat"])
def test_token_without_required_claim(client, make_user, missing):
    user = make_user()
    token = jwt.encode(
        _claims(user, **{missing: None}), get_settings().JWT_SECRET, algorithm="HS256"
    )
    assert _me(client, token).status_code == 401


def test_refresh_token_not_accepted_as_access(client, make_user):
    make_user()
    refresh_token = _login(client).json()["refresh_token"]
    assert _me(client, refresh_token).status_code == 401


def test_access_token_not_accepted_for_refresh(client, make_user):
    make_user()
    access_token = _login(client).json()["access_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


def test_token_of_deleted_user(client, make_user, db):
    user = make_user()
    token = _login(client).json()["access_token"]
    db.delete(user)
    db.commit()
    assert _me(client, token).status_code == 401


def test_token_of_deactivated_user(client, make_user, db):
    user = make_user()
    tokens = _login(client).json()
    user.is_active = False
    db.commit()
    assert _me(client, tokens["access_token"]).status_code == 401
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_logout_everywhere_revokes_tokens(client, make_user):
    make_user()
    tokens = _login(client).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/auth/logout-everywhere", headers=headers).status_code == 204
    assert _me(client, tokens["access_token"]).status_code == 401
    resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


def test_token_with_old_version(client, make_user, db):
    """Токен, выданный до смены пароля, не работает."""
    user = make_user()
    old_token = create_token(user.id, user.token_version, "access")
    user.token_version += 1
    db.commit()
    assert _me(client, old_token).status_code == 401
