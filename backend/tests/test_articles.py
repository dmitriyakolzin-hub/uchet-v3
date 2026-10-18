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


def test_seed_articles_have_valid_templates(client, headers_for, fd):
    h = headers_for(fd)
    articles = client.get("/api/articles", headers=h).json()
    assert len(articles) >= 30
    accounts = {a["id"]: a for a in client.get("/api/accounts", headers=h).json()}
    for art in articles:
        assert art["debit_account_id"] in accounts, art["name"]
        assert art["credit_account_id"] in accounts, art["name"]
        for acc_id in (art["debit_account_id"], art["credit_account_id"]):
            assert not accounts[acc_id]["has_children"], f"{art['name']}: счёт с субсчетами"
    by_name = {a["name"]: a for a in articles}
    pay = by_name["Оплата поставщикам"]
    assert (pay["cash_flow_activity"], pay["cash_flow_direction"], pay["is_pl"]) == ("operating", "outflow", False)
    assert accounts[pay["debit_account_id"]]["code"] == "60.01"
    assert accounts[pay["credit_account_id"]]["code"] == "51"
    fee = by_name["Банковские комиссии"]
    assert fee["is_cash_flow"] and fee["is_pl"] and fee["pl_section"] == "other_expense"
    assert by_name["Выручка от продаж"]["pl_section"] == "revenue"
    assert by_name["Получение кредитов и займов"]["cash_flow_activity"] == "financing"


def _body(client, h, **over):
    acc = _accounts(client, h)
    body = {"name": "Охрана", "is_pl": True, "pl_section": "admin",
            "debit_account_id": acc["26"]["id"], "credit_account_id": acc["60.01"]["id"]}
    body.update(over)
    return body


def test_admin_creates_and_edits_article(client, headers_for, admin):
    h = headers_for(admin)
    resp = client.post("/api/articles", headers=h, json=_body(client, h))
    assert resp.status_code == 201, resp.text
    art = resp.json()
    assert art["is_cash_flow"] is False and art["cash_flow_activity"] is None
    # сделать её ещё и статьёй ДДС
    resp = client.put(f"/api/articles/{art['id']}", headers=h, json=_body(
        client, h, is_cash_flow=True, cash_flow_activity="operating", cash_flow_direction="outflow"))
    assert resp.status_code == 200 and resp.json()["is_cash_flow"] is True
    # убрать из ОПиУ — раздел очищается
    resp = client.put(f"/api/articles/{art['id']}", headers=h, json=_body(
        client, h, is_pl=False, pl_section="admin", is_cash_flow=True,
        cash_flow_activity="operating", cash_flow_direction="outflow"))
    assert resp.json()["pl_section"] is None


@pytest.mark.parametrize("over", [
    {"is_pl": False},                                         # ни ДДС, ни ОПиУ
    {"pl_section": None},                                     # ОПиУ без раздела
    {"is_cash_flow": True},                                   # ДДС без вида и направления
    {"is_cash_flow": True, "cash_flow_activity": "operating"},
    {"pl_section": "marketing"},
    {"credit_account_id": None},                              # шаблон наполовину
    {"name": "  "},
])
def test_article_validation(client, headers_for, admin, over):
    h = headers_for(admin)
    assert client.post("/api/articles", headers=h, json=_body(client, h, **over)).status_code == 422


def test_template_account_with_subaccounts_rejected(client, headers_for, admin):
    h = headers_for(admin)
    resp = client.post("/api/articles", headers=h, json=_body(client, h, credit_account_id=_accounts(client, h)["60"]["id"]))
    assert resp.status_code == 422
    assert "60" in resp.json()["detail"] and "субсчет" in resp.json()["detail"]


def test_article_without_template(client, headers_for, admin):
    h = headers_for(admin)
    resp = client.post("/api/articles", headers=h,
                       json=_body(client, h, debit_account_id=None, credit_account_id=None))
    assert resp.status_code == 201


def test_duplicate_name(client, headers_for, admin):
    h = headers_for(admin)
    assert client.post("/api/articles", headers=h, json=_body(client, h, name="Аренда")).status_code == 409


def test_only_admin_changes_articles(client, headers_for, fd):
    h = headers_for(fd)
    assert client.post("/api/articles", headers=h, json=_body(client, h)).status_code == 403
    art = client.get("/api/articles", headers=h).json()[0]
    assert client.put(f"/api/articles/{art['id']}", headers=h, json=_body(client, h)).status_code == 403
