import pytest

from tests.conftest import TEST_PASSWORD


@pytest.fixture
def admin(make_user):
    return make_user(login="admin", is_admin=True)


def _create(client, h, **fields):
    body = {"login": "petrov", "full_name": "Петров П.П.", "password": "new-password-1"}
    body.update(fields)
    return client.post("/api/users", headers=h, json=body)


def test_admin_creates_user_with_roles(client, headers_for, admin, make_org):
    org = make_org()
    resp = _create(client, headers_for(admin), login="  Petrov ",
                   memberships=[{"organization_id": org.id, "role": "ceo"}])
    assert resp.status_code == 201, resp.text
    user = resp.json()
    assert user["login"] == "petrov"
    assert user["memberships"] == [{"organization_id": org.id, "organization_name": "Группа А",
                                    "role": "ceo", "cfo_id": None, "cfo_name": None, "permissions": ["view"]}]
    assert "password" not in user and "password_hash" not in user

    login = client.post("/api/auth/login", json={"login": "petrov", "password": "new-password-1"})
    assert login.status_code == 200


def test_non_admin_cannot_manage_users(client, headers_for, make_user, make_org):
    fd = make_user(login="fd")
    make_org(members=[(fd, "finance_director")])
    h = headers_for(fd)
    assert client.get("/api/users", headers=h).status_code == 403
    assert _create(client, h).status_code == 403
    assert client.patch(f"/api/users/{fd.id}", headers=h, json={"is_admin": True}).status_code == 403
    assert client.put(f"/api/users/{fd.id}/memberships", headers=h,
                      json={"memberships": []}).status_code == 403


def test_duplicate_login(client, headers_for, admin):
    assert _create(client, headers_for(admin), login="ADMIN").status_code == 409


@pytest.mark.parametrize("fields", [
    {"password": "short"}, {"login": "a"}, {"login": "иван"}, {"login": "with space"},
    {"memberships": [{"organization_id": 1, "role": "boss"}]},
])
def test_user_validation(client, headers_for, admin, make_org, fields):
    make_org()
    assert _create(client, headers_for(admin), **fields).status_code == 422


def test_unknown_organization_in_memberships(client, headers_for, admin):
    resp = _create(client, headers_for(admin), memberships=[{"organization_id": 999, "role": "ceo"}])
    assert resp.status_code == 422


def test_two_roles_in_one_organization_rejected(client, headers_for, admin, make_org):
    org = make_org()
    resp = _create(client, headers_for(admin), memberships=[
        {"organization_id": org.id, "role": "ceo"},
        {"organization_id": org.id, "role": "cfo_head"},
    ])
    assert resp.status_code == 422


def test_replace_memberships(client, headers_for, admin, make_user, make_org):
    user = make_user()
    a = make_org("А", members=[(user, "ceo")])
    b = make_org("Б")
    resp = client.put(f"/api/users/{user.id}/memberships", headers=headers_for(admin),
                      json={"memberships": [{"organization_id": b.id, "role": "finance_director"}]})
    assert resp.status_code == 200
    assert [(m["organization_id"], m["role"]) for m in resp.json()["memberships"]] == [
        (b.id, "finance_director")
    ]
    assert client.get(f"/api/organizations/{a.id}", headers=headers_for(user)).status_code == 404


def test_deactivate_user_logs_out(client, headers_for, admin, make_user):
    user = make_user()
    tokens = client.post("/api/auth/login", json={"login": "ivanov", "password": TEST_PASSWORD}).json()
    resp = client.patch(f"/api/users/{user.id}", headers=headers_for(admin), json={"is_active": False})
    assert resp.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 401


def test_admin_resets_password(client, headers_for, admin, make_user):
    user = make_user()
    old = headers_for(user)
    resp = client.patch(f"/api/users/{user.id}", headers=headers_for(admin),
                        json={"password": "reset-password-1"})
    assert resp.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401
    assert client.post("/api/auth/login",
                       json={"login": "ivanov", "password": "reset-password-1"}).status_code == 200


def test_admin_cannot_remove_own_admin_or_deactivate_self(client, headers_for, admin, make_user):
    make_user(login="admin2", is_admin=True)
    h = headers_for(admin)
    assert client.patch(f"/api/users/{admin.id}", headers=h, json={"is_admin": False}).status_code == 409
    assert client.patch(f"/api/users/{admin.id}", headers=h, json={"is_active": False}).status_code == 409


def test_other_admin_can_remove_admin_rights(client, headers_for, admin, make_user):
    other = make_user(login="admin2", is_admin=True)
    resp = client.patch(f"/api/users/{admin.id}", headers=headers_for(other), json={"is_admin": False})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is False


def test_me_shows_roles(client, headers_for, make_user, make_org):
    user = make_user()
    make_org(members=[(user, "cfo_head")])
    me = client.get("/api/auth/me", headers=headers_for(user)).json()
    assert me["is_admin"] is False
    assert me["memberships"][0]["role"] == "cfo_head"
    assert me["memberships"][0]["permissions"] == ["draft", "view"]


def test_roles_list(client, headers_for, make_user):
    roles = client.get("/api/roles", headers=headers_for(make_user())).json()
    assert [r["code"] for r in roles] == ["finance_director", "ceo", "cfo_head"]
    assert roles[0]["title"] == "Финансовый директор / владелец"


def test_change_own_password(client, headers_for, make_user):
    user = make_user()
    h = headers_for(user)
    bad = client.post("/api/auth/change-password", headers=h,
                      json={"current_password": "wrong", "new_password": "brand-new-pass"})
    assert bad.status_code == 400
    short = client.post("/api/auth/change-password", headers=h,
                        json={"current_password": TEST_PASSWORD, "new_password": "short"})
    assert short.status_code == 422

    resp = client.post("/api/auth/change-password", headers=h,
                       json={"current_password": TEST_PASSWORD, "new_password": "brand-new-pass"})
    assert resp.status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401  # старый вход завершён
    new_h = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=new_h).status_code == 200
