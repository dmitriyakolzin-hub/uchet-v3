"""Статьи ДДС и ОПиУ: справочник организации деревом (docs/TZ.md, раздел 8 и раздел 19, «Детали 9.1»)."""

import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org):
    fd, ceo, other = make_user(login="fd"), make_user(login="ceo"), make_user(login="other")
    org = make_org(members=[(fd, "finance_director"), (ceo, "ceo")])
    other_org = make_org("Клиент Б", members=[(other, "finance_director")])
    return {"h": headers_for(fd), "ceo": headers_for(ceo), "other": headers_for(other),
            "o": f"/api/organizations/{org.id}", "other_o": f"/api/organizations/{other_org.id}", "org": org}


def _accounts(client, h):
    return {a["code"]: a for a in client.get("/api/accounts", headers=h).json()}


def _articles(client, env, h=None, o=None):
    return {a["name"]: a for a in client.get(f"{o or env['o']}/articles", headers=h or env["h"]).json()}


def _body(client, h, **over):
    acc = _accounts(client, h)
    body = {"name": "Охрана", "is_pl": True, "pl_section": "admin",
            "debit_account_id": acc["26"]["id"], "credit_account_id": acc["60.01"]["id"]}
    body.update(over)
    return body


def _post(client, env, **over):
    return client.post(f"{env['o']}/articles", headers=env["h"], json=_body(client, env["h"], **over))


def test_standard_set_per_organization(client, env):
    h = env["h"]
    articles = list(_articles(client, env).values())
    assert len(articles) == 46 and [a["level"] for a in articles] == [1] * 46
    accounts = {a["id"]: a for a in client.get("/api/accounts", headers=h).json()}
    for art in articles:
        for acc_id in (art["debit_account_id"], art["credit_account_id"]):
            assert not accounts[acc_id]["has_children"], f"{art['name']}: счёт с субсчетами"
    by_name = {a["name"]: a for a in articles}
    pay = by_name["Оплата поставщикам"]
    assert (pay["cash_flow_activity"], pay["cash_flow_direction"], pay["is_pl"], pay["system_key"]) == \
        ("operating", "outflow", False, "default_out")
    assert accounts[pay["debit_account_id"]]["code"] == "60.01"
    assert by_name["Перевод между своими счетами"]["is_transfer"]
    assert by_name["Банковские комиссии"]["pl_section"] == "other_expense"

    # у другой организации — свои статьи, чужие не видны и не правятся
    other = _articles(client, env, h=env["other"], o=env["other_o"])
    assert other["Аренда"]["id"] != by_name["Аренда"]["id"]
    assert client.get(f"{env['other_o']}/articles", headers=h).status_code == 404
    assert client.put(f"/api/articles/{other['Аренда']['id']}", headers=h, json=_body(client, h)).status_code == 404


def test_create_edit_and_validation(client, env):
    h = env["h"]
    resp = _post(client, env, code="2.6.22.")
    assert resp.status_code == 201, resp.text
    art = resp.json()
    assert (art["code"], art["is_cash_flow"], art["level"], art["has_children"]) == ("2.6.22.", False, 1, False)
    resp = client.put(f"/api/articles/{art['id']}", headers=h, json=_body(
        client, h, is_cash_flow=True, cash_flow_activity="operating", cash_flow_direction="outflow"))
    assert resp.status_code == 200 and resp.json()["is_cash_flow"] is True and resp.json()["code"] is None
    assert _post(client, env, name="Аренда").status_code == 409  # такое название на верхнем уровне есть
    _post(client, env, name="Другая", code="7.1.")
    assert _post(client, env, name="Третья", code="7.1.").status_code == 409  # код не повторяется

    for over in ({"is_pl": False}, {"pl_section": None}, {"is_cash_flow": True}, {"pl_section": "marketing"},
                 {"credit_account_id": None}, {"name": "  "}):
        assert _post(client, env, **{"name": "Проверка", **over}).status_code == 422, over
    resp = _post(client, env, name="Проверка", credit_account_id=_accounts(client, h)["60"]["id"])
    assert resp.status_code == 422 and "субсчет" in resp.json()["detail"]
    assert _post(client, env, name="Без шаблона", debit_account_id=None, credit_account_id=None).status_code == 201
    assert _post(client, env, name="Перевод 2", is_transfer=True).status_code == 422

    # права: CEO смотрит, но не правит
    assert client.get(f"{env['o']}/articles", headers=env["ceo"]).status_code == 200
    assert client.post(f"{env['o']}/articles", headers=env["ceo"], json=_body(client, h)).status_code == 403


def test_tree(client, env, db):
    h, arts = env["h"], _articles(client, env)
    rent = arts["Аренда"]
    acc = _accounts(client, h)
    # подстатьи наследуют признаки и шаблон родителя
    fixed = _post(client, env, name="постоянная часть", parent_id=rent["id"], code="2.3.1.",
                  is_pl=False, is_cash_flow=True, cash_flow_activity="investing", cash_flow_direction="inflow",
                  debit_account_id=None, credit_account_id=None)
    assert fixed.status_code == 201, fixed.text
    fixed = fixed.json()
    assert (fixed["is_pl"], fixed["pl_section"], fixed["is_cash_flow"], fixed["level"]) == (True, "selling", False, 2)
    assert (fixed["debit_account_id"], fixed["credit_account_id"]) == (rent["debit_account_id"], rent["credit_account_id"])
    variable = _post(client, env, name="переменная часть", parent_id=rent["id"]).json()
    gas = _post(client, env, name="газ", parent_id=variable["id"]).json()
    assert gas["level"] == 3
    assert _post(client, env, name="газ", parent_id=variable["id"]).status_code == 409
    assert _post(client, env, name="газ", parent_id=fixed["id"]).status_code == 201  # в другой группе можно
    assert _articles(client, env)["Аренда"]["has_children"] is True

    # раздел группы меняется — меняется у всей ветки
    body = {**_body(client, h, name="Аренда", pl_section="admin"), "debit_account_id": rent["debit_account_id"],
            "credit_account_id": rent["credit_account_id"], "sort_order": rent["sort_order"]}
    assert client.put(f"/api/articles/{rent['id']}", headers=h, json=body).status_code == 200
    assert _articles(client, env)["газ"]["pl_section"] == "admin"

    # перенос внутрь своей ветки запрещён; системная статья не бывает группой
    move = {**_body(client, h, name="Аренда"), "parent_id": gas["id"]}
    assert client.put(f"/api/articles/{rent['id']}", headers=h, json=move).status_code == 422
    salary = arts["Выплата заработной платы"]
    resp = _post(client, env, name="аванс", parent_id=salary["id"])
    assert resp.status_code == 422 and "системная" in resp.json()["detail"]
    assert _post(client, env, name="чужая", parent_id=_articles(client, env, env["other"], env["other_o"])["Аренда"]["id"]
                 ).status_code == 422
    # группу в архив — только когда подстатьи в архиве
    resp = client.put(f"/api/articles/{variable['id']}", headers=h,
                      json={**_body(client, h, name="переменная часть", parent_id=rent["id"]), "is_active": False})
    assert resp.status_code == 422

    # в операцию ставится только статья без подстатей
    from app.models import LegalEntity

    le = LegalEntity(organization_id=env["org"].id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.commit()
    cfo = client.post(f"{env['o']}/cfos", headers=h, json={"name": "Офис"}).json()
    cp = client.post(f"{env['o']}/counterparties", headers=h, json={"name": "Арендодатель", "kind": "legal",
                                                                    "inn": "7702222222"}).json()
    rub = client.get("/api/currencies", headers=h).json()[0]["id"]
    contract = client.post(f"{env['o']}/contracts", headers=h, json={
        "counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub, "kind": "supplier", "number": "1"}).json()

    def operation(article):
        return client.post(f"{env['o']}/operations", headers=h, json={
            "legal_entity_id": le.id, "op_date": "2026-03-01", "article_id": article["id"], "postings": [{
                "debit_account_id": acc["44"]["id"], "credit_account_id": acc["60.01"]["id"], "amount": "100",
                "debit": {"cfo": cfo["id"], "pl_item": article["id"]},
                "credit": {"counterparty": cp["id"], "contract": contract["id"]}}]})

    resp = operation(variable)
    assert resp.status_code == 422 and "группа" in resp.json()["detail"]
    assert operation(gas).status_code == 201
