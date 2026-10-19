import pytest


@pytest.fixture
def admin(make_user):
    return make_user(login="admin", is_admin=True)


@pytest.fixture
def fd(make_user, make_org):
    user = make_user(login="fd")
    make_org(members=[(user, "finance_director")])
    return user


def _accounts(client, h):
    return {a["code"]: a for a in client.get("/api/accounts", headers=h).json()}


def test_seed_chart_and_analytics(client, headers_for, fd):
    acc = _accounts(client, headers_for(fd))  # смотреть может любой пользователь
    assert len(acc) >= 45
    assert acc["51"]["analytics"] == ["bank_account", "cash_flow_item"]
    assert acc["50"]["analytics"] == ["cfo", "cash_flow_item"]
    assert acc["60"]["analytics"] == ["counterparty", "contract"]
    assert acc["60.01"]["analytics"] == ["counterparty", "contract"]  # субсчёт унаследовал
    assert acc["70"]["analytics"] == ["employee"]
    assert acc["41"]["analytics"] == ["nomenclature", "warehouse"] and acc["41"]["is_quantitative"]
    assert acc["90.01"]["analytics"] == ["cfo", "pl_item"]
    assert acc["52"]["is_currency"] and not acc["51"]["is_currency"]
    assert acc["60"]["has_children"] and not acc["60.01"]["has_children"]
    assert acc["60.01"]["parent_id"] == acc["60"]["id"]


def test_accounts_sorted_by_code(client, headers_for, fd):
    codes = [a["code"] for a in client.get("/api/accounts", headers=headers_for(fd)).json()]
    assert codes.index("01") < codes.index("02") < codes.index("10") < codes.index("60")
    assert codes.index("60") < codes.index("60.01") < codes.index("60.02") < codes.index("62")


def test_admin_adds_subaccount_inheriting_parent(client, headers_for, admin):
    h = headers_for(admin)
    resp = client.post("/api/accounts", headers=h, json={"code": "76.05", "name": "Расчёты с арендодателями"})
    assert resp.status_code == 201, resp.text
    sub = resp.json()
    assert sub["analytics"] == ["counterparty", "contract"]
    assert sub["account_type"] == "active_passive"
    assert _accounts(client, h)["76"]["has_children"]


def test_subaccount_with_own_analytics(client, headers_for, admin):
    resp = client.post("/api/accounts", headers=headers_for(admin),
                       json={"code": "76.06", "name": "Прочее", "analytics": ["counterparty"]})
    assert resp.json()["analytics"] == ["counterparty"]


def test_second_level_subaccount(client, headers_for, admin):
    h = headers_for(admin)
    assert client.post("/api/accounts", headers=h, json={"code": "76.07", "name": "A"}).status_code == 201
    resp = client.post("/api/accounts", headers=h, json={"code": "76.07.01", "name": "B"})
    assert resp.status_code == 201
    assert resp.json()["parent_id"] == _accounts(client, h)["76.07"]["id"]


def test_subaccount_requires_parent(client, headers_for, admin):
    resp = client.post("/api/accounts", headers=headers_for(admin), json={"code": "77.01", "name": "x"})
    assert resp.status_code == 422
    assert "77" in resp.json()["detail"]


def test_new_top_account_requires_type(client, headers_for, admin):
    h = headers_for(admin)
    assert client.post("/api/accounts", headers=h, json={"code": "77", "name": "x"}).status_code == 422
    resp = client.post("/api/accounts", headers=h,
                       json={"code": "77", "name": "Отложенные налоговые обязательства", "account_type": "passive"})
    assert resp.status_code == 201


@pytest.mark.parametrize("code", ["6", "600", "60.1", "60.001", "60.01.01.01", "6a"])
def test_bad_code(client, headers_for, admin, code):
    resp = client.post("/api/accounts", headers=headers_for(admin),
                       json={"code": code, "name": "x", "account_type": "active"})
    assert resp.status_code == 422


def test_duplicate_code(client, headers_for, admin):
    resp = client.post("/api/accounts", headers=headers_for(admin), json={"code": "60.01", "name": "x"})
    assert resp.status_code == 409


def test_unknown_or_repeated_analytic_kind(client, headers_for, admin):
    h = headers_for(admin)
    for analytics in (["partner"], ["counterparty", "counterparty"]):
        resp = client.post("/api/accounts", headers=h,
                           json={"code": "76.08", "name": "x", "analytics": analytics})
        assert resp.status_code == 422


def test_admin_changes_analytics_and_deactivates(client, headers_for, admin):
    h = headers_for(admin)
    sub = client.post("/api/accounts", headers=h, json={"code": "76.09", "name": "x"}).json()
    resp = client.patch(f"/api/accounts/{sub['id']}", headers=h,
                        json={"analytics": ["contract", "counterparty", "cfo"], "is_active": False, "name": "y"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["analytics"] == ["contract", "counterparty", "cfo"]  # порядок сохраняется
    assert body["is_active"] is False and body["name"] == "y"
    resp = client.patch(f"/api/accounts/{sub['id']}", headers=h, json={"analytics": []})
    assert resp.json()["analytics"] == []


def test_code_cannot_be_changed(client, headers_for, admin):
    h = headers_for(admin)
    sub = client.post("/api/accounts", headers=h, json={"code": "76.10", "name": "x"}).json()
    client.patch(f"/api/accounts/{sub['id']}", headers=h, json={"code": "76.11"})
    assert _accounts(client, h)["76.10"]["id"] == sub["id"]


def test_only_admin_changes_chart(client, headers_for, fd):
    h = headers_for(fd)
    assert client.post("/api/accounts", headers=h, json={"code": "76.12", "name": "x"}).status_code == 403
    acc = _accounts(client, h)["51"]
    assert client.patch(f"/api/accounts/{acc['id']}", headers=h, json={"name": "x"}).status_code == 403


def test_analytic_kinds(client, headers_for, fd):
    kinds = client.get("/api/analytic-kinds", headers=headers_for(fd)).json()
    assert {"code": "cash_flow_item", "title": "Статья ДДС"} in kinds
    assert len(kinds) == 11
