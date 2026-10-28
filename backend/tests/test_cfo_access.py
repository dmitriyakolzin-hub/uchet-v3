"""Руководитель ЦФО видит и вводит данные только своей ветки дерева ЦФО (docs/TZ.md, раздел 13)."""

import pytest

from tests.test_excel_import import OP_HEADER, XLSX, op_row, xlsx
from tests.test_users import admin  # noqa: F401


@pytest.fixture
def env(client, headers_for, make_user, make_org, db, admin):
    from app.models import Bank, Currency, LegalEntity, Membership

    fd = make_user(login="fd")
    head = make_user(login="head")
    lonely = make_user(login="lonely")  # руководитель ЦФО без назначенного ЦФО
    org = make_org("Группа А", members=[(fd, "finance_director"), (lonely, "cfo_head")])
    other = make_org("Клиент Б")
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    bank = Bank(bic="044525225", name="Сбер")
    db.add_all([le, bank])
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    rub = db.query(Currency).filter_by(code="RUB").one()

    def post(path, body, org_path=o):
        resp = client.post(f"{org_path}/{path}", headers=h if org_path == o else headers_for(admin), json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    company = post("cfos", {"name": "Компания"})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"]})
    shop = post("cfos", {"name": "Магазин 1", "parent_id": trade["id"]})
    logistics = post("cfos", {"name": "Логистика", "parent_id": company["id"]})
    foreign = post("cfos", {"name": "Чужой"}, org_path=f"/api/organizations/{other.id}")
    cp = post("counterparties", {"name": "Поставщик", "kind": "legal", "inn": "7702222222"})
    contract = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub.id,
                                  "kind": "supplier", "number": "1"})
    bank_acc = post("bank-accounts", {"legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id,
                                      "number": "40702810900000012345", "name": "Сбер"})
    resp = client.put(f"/api/users/{head.id}/memberships", headers=headers_for(admin), json={
        "memberships": [{"organization_id": org.id, "role": "cfo_head", "cfo_id": trade["id"]}]})
    assert resp.status_code == 200, resp.text
    assert db.query(Membership).filter_by(user_id=head.id).one().cfo_id == trade["id"]
    acc = {a["code"]: a["id"] for a in client.get("/api/accounts", headers=h).json()}
    art = {a["name"]: a["id"] for a in client.get(f"/api/organizations/{org.id}/articles", headers=h).json()}
    return {"org": org, "other": other, "le": le, "o": o, "h": h, "hh": headers_for(head),
            "lonely": headers_for(lonely), "company": company, "trade": trade, "shop": shop,
            "logistics": logistics, "foreign": foreign, "cp": cp, "contract": contract, "bank_acc": bank_acc,
            "acc": acc, "art": art, "admin": headers_for(admin), "head": head}


def posting(env, cfo, amount="100"):
    return {"debit_account_id": env["acc"]["26"], "credit_account_id": env["acc"]["60.01"], "amount": amount,
            "debit": {"cfo": cfo["id"], "pl_item": env["art"]["Связь и IT"]},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}


def body(env, *postings, description="Связь"):
    return {"legal_entity_id": env["le"].id, "op_date": "2026-10-05", "description": description,
            "postings": list(postings)}


def create(client, env, data, h=None, post=False):
    return client.post(f"{env['o']}/operations{'?post=true' if post else ''}", headers=h or env["h"], json=data)


def test_membership_requires_cfo_of_same_org(client, env):
    url = f"/api/users/{env['head'].id}/memberships"
    no_cfo = client.put(url, headers=env["admin"], json={"memberships": [
        {"organization_id": env["org"].id, "role": "cfo_head"}]})
    assert no_cfo.status_code == 422
    foreign = client.put(url, headers=env["admin"], json={"memberships": [
        {"organization_id": env["org"].id, "role": "cfo_head", "cfo_id": env["foreign"]["id"]}]})
    assert foreign.status_code == 422
    me = client.get("/api/auth/me", headers=env["hh"]).json()
    assert (me["memberships"][0]["cfo_id"], me["memberships"][0]["cfo_name"]) == (env["trade"]["id"], "Торговля")
    org = client.get(f"/api/organizations/{env['org'].id}", headers=env["hh"]).json()
    assert org["restricted"] is True and org["cfo_scope"] == env["trade"]["id"]
    fd_org = client.get(f"/api/organizations/{env['org'].id}", headers=env["h"]).json()
    assert fd_org["restricted"] is False and fd_org["cfo_scope"] is None


def test_journal_limited_to_branch(client, env):
    mine = create(client, env, body(env, posting(env, env["shop"]), description="мой"), post=True).json()
    alien = create(client, env, body(env, posting(env, env["logistics"]), description="чужой"), post=True).json()
    no_cfo = create(client, env, {**body(env), "postings": [{
        "debit_account_id": env["acc"]["60.01"], "credit_account_id": env["acc"]["51"], "amount": "5",
        "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
        "credit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": env["art"]["Оплата поставщикам"]}}]})
    assert no_cfo.status_code == 201

    items = client.get(f"{env['o']}/operations", headers=env["hh"]).json()["items"]
    assert [i["description"] for i in items] == ["мой"]
    assert client.get(f"/api/operations/{mine['id']}", headers=env["hh"]).status_code == 200
    assert client.get(f"/api/operations/{alien['id']}", headers=env["hh"]).status_code == 404
    assert client.get(f"/api/operations/{alien['id']}/history", headers=env["hh"]).status_code == 404

    # черновики — только с ЦФО своей ветки и без чужих
    assert create(client, env, body(env, posting(env, env["shop"])), h=env["hh"]).status_code == 201
    assert create(client, env, body(env, posting(env, env["logistics"])), h=env["hh"]).status_code == 403
    mixed = body(env, posting(env, env["shop"]), posting(env, env["logistics"]))
    assert create(client, env, mixed, h=env["hh"]).status_code == 403
    assert create(client, env, body(env, no_cfo.json()["postings"][0] | {
        "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
        "credit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": env["art"]["Оплата поставщикам"]},
    }), h=env["hh"]).status_code == 403

    # своё не переписать на чужой ЦФО
    own = create(client, env, body(env, posting(env, env["trade"])), h=env["hh"]).json()
    resp = client.put(f"/api/operations/{own['id']}", headers=env["hh"], json=body(env, posting(env, env["logistics"])))
    assert resp.status_code == 403
    bulk = client.post(f"{env['o']}/operations/bulk", headers=env["hh"],
                       json={"action": "delete", "ids": [alien["id"], own["id"]]}).json()
    assert [r["ok"] for r in bulk["results"]] == [False, True]
    assert bulk["results"][0]["error"] == "Операция не найдена"


def test_reports_limited_to_branch(client, env):
    create(client, env, body(env, posting(env, env["shop"], "300")), post=True)
    create(client, env, body(env, posting(env, env["logistics"], "900")), post=True)
    params = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    url = f"{env['o']}/reports/osv"
    rows = {r["code"]: r for r in client.get(url, headers=env["hh"], params=params).json()["rows"]}
    assert rows["26"]["debit"] == "300.00"
    assert client.get(url, headers=env["hh"], params={**params, "cfo_id": env["logistics"]["id"]}).status_code == 404
    shop = client.get(url, headers=env["hh"], params={**params, "cfo_id": env["shop"]["id"]})
    assert shop.status_code == 200
    everything = {r["code"]: r for r in client.get(url, headers=env["h"], params=params).json()["rows"]}
    assert everything["26"]["debit"] == "1200.00"
    pl = client.get(f"{env['o']}/reports/pl", headers=env["hh"], params=params).json()
    assert {r["key"]: r for r in pl["rows"]}["net"]["values"]["total"] == "-300.00"


def test_money_and_service_pages(client, env):
    cash = {"debit_account_id": env["acc"]["50"], "credit_account_id": env["acc"]["51"], "amount": "50",
            "debit": {"cfo": env["shop"]["id"], "cash_flow_item": env["art"]["Перевод между своими счетами"]},
            "credit": {"bank_account": env["bank_acc"]["id"],
                       "cash_flow_item": env["art"]["Перевод между своими счетами"]}}
    create(client, env, body(env, cash), post=True)
    params = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    rows = client.get(f"{env['o']}/money", headers=env["hh"], params=params).json()["rows"]
    assert [(r["kind"], r["cfo_id"]) for r in rows] == [("cash", env["shop"]["id"])]
    assert len(client.get(f"{env['o']}/money", headers=env["h"], params=params).json()["rows"]) == 2
    mv = f"{env['o']}/money/movements"
    assert client.get(mv, headers=env["hh"], params={**params, "cfo_id": env["shop"]["id"]}).status_code == 200
    assert client.get(mv, headers=env["hh"], params={**params, "bank_account_id": env["bank_acc"]["id"]}).status_code == 404
    assert client.get(f"{env['o']}/opening-balances", headers=env["hh"]).status_code == 403
    resp = client.post(f"{env['o']}/bank-statements", headers={**env["hh"], "Content-Type": "application/octet-stream"},
                       content=b"1CClientBankExchange")
    assert resp.status_code == 403


def test_excel_import_only_branch(client, env):
    def row(cfo):
        return op_row(Дата="05.10.2026", Статья="Связь и IT", **{
            "Счёт Дт": "26", "Счёт Кт": "60.01", "Сумма": 10, "ЦФО": cfo,
            "Контрагент (ИНН или наименование)": "7702222222", "Договор №": "1"})
    raw = xlsx(OP_HEADER, [row("Магазин 1"), row("Логистика")])
    resp = client.post(f"{env['o']}/import/operations", headers={**env["hh"], "Content-Type": XLSX}, content=raw)
    result = resp.json()
    assert result["created"] == [1] and [e["row"] for e in result["errors"]] == [3]
    assert "вашей ветки" in result["errors"][0]["error"]


def test_cfo_head_without_cfo_sees_nothing(client, env):
    assert client.get(f"{env['o']}/operations", headers=env["lonely"]).status_code == 403
    params = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
    assert client.get(f"{env['o']}/reports/osv", headers=env["lonely"], params=params).status_code == 403
    org = client.get(f"/api/organizations/{env['org'].id}", headers=env["lonely"]).json()
    assert org["restricted"] is True
