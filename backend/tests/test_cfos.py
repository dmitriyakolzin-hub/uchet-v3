import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.commit()
    h = headers_for(fd)

    def add(name, parent=None, **extra):
        body = {"name": name, "parent_id": parent["id"] if parent else None, **extra}
        return client.post(f"/api/organizations/{org.id}/cfos", headers=h, json=body)

    return {"org": org, "other": other, "le": le, "h": h, "ceo": ceo, "other_fd": other_fd, "add": add,
            "headers_for": headers_for}


def _tree(env, client):
    """Пять уровней: Компания → Торговля → Филиал Юг → Магазин 1 → Касса"""
    add = env["add"]
    company = add("АВМВ").json()
    trade = add("Торговля", company).json()
    branch = add("Филиал Юг", trade).json()
    shop = add("Магазин 1", branch).json()
    unit = add("Касса", shop).json()
    return company, trade, branch, shop, unit


def test_five_levels_and_paths(client, env):
    company, trade, branch, shop, unit = _tree(env, client)
    assert [c["level"] for c in (company, trade, branch, shop, unit)] == [1, 2, 3, 4, 5]
    assert unit["path"] == ".".join(str(c["id"]) for c in (company, trade, branch, shop, unit))
    assert company["parent_id"] is None


def test_no_sixth_level(client, env):
    *_, unit = _tree(env, client)
    resp = env["add"]("Слишком глубоко", unit)
    assert resp.status_code == 422
    assert "Подразделение учёта" in resp.json()["detail"]


def test_legal_entity_only_on_company(client, env):
    company = env["add"]("АВМВ", legal_entity_id=env["le"].id)
    assert company.status_code == 201
    resp = env["add"]("Торговля", company.json(), legal_entity_id=env["le"].id)
    assert resp.status_code == 422


def test_cannot_skip_level_when_moving(client, env):
    company, trade, branch, shop, unit = _tree(env, client)
    # магазин нельзя положить прямо в направление
    resp = client.patch(f"/api/cfos/{shop['id']}", headers=env["h"], json={"parent_id": trade["id"]})
    assert resp.status_code == 422
    assert "Филиал" in resp.json()["detail"]
    # и нельзя сделать корнем
    resp = client.patch(f"/api/cfos/{shop['id']}", headers=env["h"], json={"parent_id": None})
    assert resp.status_code == 422


def test_move_branch_moves_subtree(client, env):
    company, trade, branch, shop, unit = _tree(env, client)
    online = env["add"]("Онлайн", company).json()
    resp = client.patch(f"/api/cfos/{branch['id']}", headers=env["h"], json={"parent_id": online["id"]})
    assert resp.status_code == 200, resp.text
    cfos = {c["id"]: c for c in client.get(f"/api/organizations/{env['org'].id}/cfos", headers=env["h"]).json()}
    expected_prefix = f"{company['id']}.{online['id']}.{branch['id']}"
    assert cfos[branch["id"]]["path"] == expected_prefix
    assert cfos[shop["id"]]["path"] == f"{expected_prefix}.{shop['id']}"
    assert cfos[unit["id"]]["path"] == f"{expected_prefix}.{shop['id']}.{unit['id']}"
    assert cfos[trade["id"]]["path"] == f"{company['id']}.{trade['id']}"


def test_archive_requires_children_archived(client, env):
    company, trade, *_ = _tree(env, client)
    resp = client.patch(f"/api/cfos/{trade['id']}", headers=env["h"], json={"is_active": False})
    assert resp.status_code == 422


def test_foreign_parent_rejected(client, env):
    other_company = client.post(f"/api/organizations/{env['other'].id}/cfos",
                                headers=env["headers_for"](env["other_fd"]), json={"name": "Чужая"}).json()
    resp = env["add"]("Направление", other_company)
    assert resp.status_code == 422


def test_ceo_sees_tree_but_cannot_edit(client, env):
    company, *_ = _tree(env, client)
    h = env["headers_for"](env["ceo"])
    assert len(client.get(f"/api/organizations/{env['org'].id}/cfos", headers=h).json()) == 5
    assert client.post(f"/api/organizations/{env['org'].id}/cfos", headers=h, json={"name": "x"}).status_code == 403


def test_subtree_query_with_ltree(client, env, db):
    """Отбор ветки по пути — понадобится для доступа руководителей ЦФО (этап 3)."""
    from sqlalchemy import text

    company, trade, branch, shop, unit = _tree(env, client)
    ids = db.execute(
        text("SELECT id FROM cfos WHERE path <@ CAST(:p AS ltree) ORDER BY level"), {"p": branch["path"]}
    ).scalars().all()
    assert ids == [branch["id"], shop["id"], unit["id"]]


# --- Сотрудники ---


def test_employee(client, env):
    company, trade, branch, shop, unit = _tree(env, client)
    url = f"/api/organizations/{env['org'].id}/employees"
    body = {"full_name": "Иванов Иван", "personnel_number": "0001", "inn": "770111111111",
            "position": "Продавец", "cfo_id": shop["id"], "hired_on": "2026-01-10"}
    resp = client.post(url, headers=env["h"], json=body)
    assert resp.status_code == 201, resp.text
    assert client.post(url, headers=env["h"], json={**body, "full_name": "Другой"}).status_code == 409
    # без табельного номера — можно сколько угодно
    for name in ("Петров", "Сидоров"):
        assert client.post(url, headers=env["h"], json={"full_name": name}).status_code == 201
    emp = resp.json()
    bad = client.patch(f"/api/employees/{emp['id']}", headers=env["h"], json={"fired_on": "2025-12-31"})
    assert bad.status_code == 422
    ok = client.patch(f"/api/employees/{emp['id']}", headers=env["h"], json={"fired_on": "2026-06-30", "cfo_id": None})
    assert ok.status_code == 200 and ok.json()["cfo_id"] is None


@pytest.mark.parametrize("body", [
    {"full_name": " "}, {"full_name": "X", "inn": "7701111111"},
    {"full_name": "X", "hired_on": "2026-02-01", "fired_on": "2026-01-01"},
])
def test_employee_validation(client, env, body):
    resp = client.post(f"/api/organizations/{env['org'].id}/employees", headers=env["h"], json=body)
    assert resp.status_code == 422
