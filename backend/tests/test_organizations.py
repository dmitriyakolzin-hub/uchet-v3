import pytest


@pytest.fixture
def admin(make_user):
    return make_user(login="admin", is_admin=True)


@pytest.fixture
def setup(make_user, make_org, admin):
    """Две организации и пользователи с разными ролями в первой."""
    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    head = make_user(login="head")
    stranger = make_user(login="stranger")
    org_a = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo"), (head, "cfo_head")])
    org_b = make_org("Клиент Б", kind="client", members=[(stranger, "finance_director")])
    return {"admin": admin, "fd": fd, "ceo": ceo, "head": head, "stranger": stranger,
            "a": org_a, "b": org_b}


ENTITY = {"full_name": "ООО «Ромашка»", "short_name": "Ромашка", "inn": "7701234567",
          "kpp": "770101001", "ogrn": "1027700000000"}


# --- Создание организаций ---


def test_admin_creates_organization(client, headers_for, admin):
    resp = client.post("/api/organizations", headers=headers_for(admin),
                       json={"name": "  Новая  ", "kind": "client"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Новая"
    assert resp.json()["is_active"] is True


@pytest.mark.parametrize("who", ["fd", "ceo", "head"])
def test_only_admin_creates_organization(client, headers_for, setup, who):
    resp = client.post("/api/organizations", headers=headers_for(setup[who]),
                       json={"name": "Новая", "kind": "group"})
    assert resp.status_code == 403


def test_duplicate_organization_name(client, headers_for, setup):
    resp = client.post("/api/organizations", headers=headers_for(setup["admin"]),
                       json={"name": "Группа А", "kind": "group"})
    assert resp.status_code == 409


def test_bad_organization_kind(client, headers_for, admin):
    resp = client.post("/api/organizations", headers=headers_for(admin),
                       json={"name": "X", "kind": "other"})
    assert resp.status_code == 422


# --- Видимость ---


def test_admin_sees_all(client, headers_for, setup):
    names = [o["name"] for o in client.get("/api/organizations", headers=headers_for(setup["admin"])).json()]
    assert names == ["Группа А", "Клиент Б"]


@pytest.mark.parametrize("who", ["fd", "ceo", "head"])
def test_member_sees_only_own(client, headers_for, setup, who):
    orgs = client.get("/api/organizations", headers=headers_for(setup[who])).json()
    assert [o["name"] for o in orgs] == ["Группа А"]


def test_permissions_in_list(client, headers_for, setup):
    by_user = {
        who: client.get("/api/organizations", headers=headers_for(setup[who])).json()[0]["permissions"]
        for who in ("fd", "ceo", "head")
    }
    assert by_user["fd"] == ["draft", "edit_reference", "post", "view"]
    assert by_user["ceo"] == ["view"]
    assert by_user["head"] == ["draft", "view"]


def test_user_without_roles_sees_nothing(client, headers_for, make_user, setup):
    nobody = make_user(login="nobody")
    assert client.get("/api/organizations", headers=headers_for(nobody)).json() == []


def test_foreign_organization_is_not_found(client, headers_for, setup):
    """Чужая организация — 404, а не 403: не раскрываем, что она существует."""
    h = headers_for(setup["fd"])
    assert client.get(f"/api/organizations/{setup['b'].id}", headers=h).status_code == 404
    assert client.get(f"/api/organizations/{setup['b'].id}/legal-entities", headers=h).status_code == 404
    assert client.patch(f"/api/organizations/{setup['b'].id}", headers=h,
                        json={"name": "Взлом"}).status_code == 404


# --- Изменение организации ---


def test_fd_edits_own_organization(client, headers_for, setup):
    resp = client.patch(f"/api/organizations/{setup['a'].id}", headers=headers_for(setup["fd"]),
                        json={"note": "Основная группа", "is_active": False})
    assert resp.status_code == 200
    assert resp.json()["note"] == "Основная группа"
    assert resp.json()["is_active"] is False


@pytest.mark.parametrize("who", ["ceo", "head"])
def test_viewers_cannot_edit_organization(client, headers_for, setup, who):
    resp = client.patch(f"/api/organizations/{setup['a'].id}", headers=headers_for(setup[who]),
                        json={"note": "x"})
    assert resp.status_code == 403


# --- Юрлица ---


def test_fd_creates_and_edits_legal_entity(client, headers_for, setup):
    h = headers_for(setup["fd"])
    org_id = setup["a"].id
    resp = client.post(f"/api/organizations/{org_id}/legal-entities", headers=h, json=ENTITY)
    assert resp.status_code == 201, resp.text
    entity = resp.json()

    resp = client.patch(f"/api/legal-entities/{entity['id']}", headers=h,
                        json={"short_name": "Ромашка-2", "kpp": None})
    assert resp.status_code == 200
    assert resp.json()["short_name"] == "Ромашка-2"
    assert resp.json()["kpp"] is None
    assert resp.json()["inn"] == ENTITY["inn"]

    listed = client.get(f"/api/organizations/{org_id}/legal-entities", headers=h).json()
    assert [e["short_name"] for e in listed] == ["Ромашка-2"]


@pytest.mark.parametrize("who", ["ceo", "head"])
def test_viewers_see_but_cannot_change_legal_entities(client, headers_for, setup, who):
    org_id = setup["a"].id
    created = client.post(f"/api/organizations/{org_id}/legal-entities",
                          headers=headers_for(setup["fd"]), json=ENTITY).json()
    h = headers_for(setup[who])
    assert client.get(f"/api/organizations/{org_id}/legal-entities", headers=h).status_code == 200
    assert client.post(f"/api/organizations/{org_id}/legal-entities", headers=h,
                       json={**ENTITY, "inn": "7709876543"}).status_code == 403
    assert client.patch(f"/api/legal-entities/{created['id']}", headers=h,
                        json={"short_name": "x"}).status_code == 403


def test_foreign_legal_entity_is_not_found(client, headers_for, setup):
    created = client.post(f"/api/organizations/{setup['b'].id}/legal-entities",
                          headers=headers_for(setup["stranger"]), json=ENTITY).json()
    resp = client.patch(f"/api/legal-entities/{created['id']}", headers=headers_for(setup["fd"]),
                        json={"short_name": "Взлом"})
    assert resp.status_code == 404


def test_same_inn_twice_in_organization(client, headers_for, setup):
    h = headers_for(setup["fd"])
    url = f"/api/organizations/{setup['a'].id}/legal-entities"
    assert client.post(url, headers=h, json=ENTITY).status_code == 201
    assert client.post(url, headers=h, json=ENTITY).status_code == 409


def test_same_inn_in_different_organizations_allowed(client, headers_for, setup):
    h = headers_for(setup["admin"])
    for org in (setup["a"], setup["b"]):
        resp = client.post(f"/api/organizations/{org.id}/legal-entities", headers=h, json=ENTITY)
        assert resp.status_code == 201


@pytest.mark.parametrize("field,value", [
    ("inn", "123"), ("inn", "77012345678"), ("inn", "77012345ab"),
    ("kpp", "12345678"), ("ogrn", "123"),
])
def test_legal_entity_validation(client, headers_for, setup, field, value):
    resp = client.post(f"/api/organizations/{setup['a'].id}/legal-entities",
                       headers=headers_for(setup["fd"]), json={**ENTITY, field: value})
    assert resp.status_code == 422


def test_individual_entrepreneur_inn_12(client, headers_for, setup):
    resp = client.post(f"/api/organizations/{setup['a'].id}/legal-entities",
                       headers=headers_for(setup["fd"]),
                       json={"full_name": "ИП Иванов И.И.", "short_name": "ИП Иванов",
                             "inn": "770123456789", "ogrn": "304770000000000"})
    assert resp.status_code == 201


def test_role_removed_access_lost_immediately(client, headers_for, setup, db):
    """Права проверяются по базе в каждом запросе: снятая роль действует сразу, без перевхода."""
    from app.models import Membership

    h = headers_for(setup["ceo"])
    assert client.get(f"/api/organizations/{setup['a'].id}", headers=h).status_code == 200
    db.query(Membership).filter_by(user_id=setup["ceo"].id).delete()
    db.commit()
    assert client.get(f"/api/organizations/{setup['a'].id}", headers=h).status_code == 404
