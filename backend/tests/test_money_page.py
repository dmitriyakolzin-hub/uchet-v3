"""«Деньги» и «Начальные остатки»."""

from decimal import Decimal

import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import Bank, Currency, LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    bank = Bank(bic="044525225", name="Сбер")
    db.add_all([le, bank])
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    rub = db.query(Currency).filter_by(code="RUB").one()

    def post(path, body):
        resp = client.post(f"{o}/{path}", headers=h, json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    cp = post("counterparties", {"name": "Поставщик", "kind": "legal", "inn": "7702222222"})
    contract = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub.id,
                                  "kind": "supplier", "number": "1"})
    acc_main = post("bank-accounts", {"legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id,
                                      "number": "40702810900000000001", "name": "Основной"})
    acc_extra = post("bank-accounts", {"legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id,
                                       "number": "40702810900000000002", "name": "Дополнительный"})
    shop = post("cfos", {"name": "Магазин"})
    accounts = {a["code"]: a["id"] for a in client.get("/api/accounts", headers=h).json()}
    articles = {a["name"]: a["id"] for a in client.get("/api/articles", headers=h).json()}
    return {"org": org, "other": other, "le": le, "cp": cp, "contract": contract, "main": acc_main,
            "extra": acc_extra, "shop": shop, "acc": accounts, "art": articles, "h": h, "o": o,
            "headers_for": headers_for, "ceo": ceo, "other_fd": other_fd}


def op(client, env, postings, op_date="2026-10-05", post=True, **extra):
    body = {"legal_entity_id": env["le"].id, "op_date": op_date, "postings": postings, **extra}
    resp = client.post(f"{env['o']}/operations{'?post=true' if post else ''}", headers=env["h"], json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def pay(env, amount, bank="main"):
    """Оплата поставщику с расчётного счёта."""
    return {"debit_account_id": env["acc"]["60.01"], "credit_account_id": env["acc"]["51"], "amount": amount,
            "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
            "credit": {"bank_account": env[bank]["id"], "cash_flow_item": env["art"]["Оплата поставщикам"]}}


def receive(env, amount, bank="main"):
    """Возврат от поставщика на расчётный счёт."""
    return {"debit_account_id": env["acc"]["51"], "credit_account_id": env["acc"]["60.01"], "amount": amount,
            "debit": {"bank_account": env[bank]["id"], "cash_flow_item": env["art"]["Возврат средств от поставщиков"]},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}


def transfer(env, amount):
    """Перевод с основного счёта на дополнительный."""
    art = env["art"]["Перевод между своими счетами"]
    return {"debit_account_id": env["acc"]["51"], "credit_account_id": env["acc"]["51"], "amount": amount,
            "debit": {"bank_account": env["extra"]["id"], "cash_flow_item": art},
            "credit": {"bank_account": env["main"]["id"], "cash_flow_item": art}}


def money(client, env, date_from="2026-10-01", date_to="2026-10-31", h=None):
    resp = client.get(f"{env['o']}/money", headers=h or env["h"], params={"date_from": date_from, "date_to": date_to})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_overview_balances(client, env):
    op(client, env, [receive(env, "1000")], op_date="2026-09-20")      # до периода
    op(client, env, [receive(env, "500"), pay(env, "300")])              # в периоде
    op(client, env, [transfer(env, "200")])
    op(client, env, [pay(env, "999")], post=False)                       # черновик — не считается
    op(client, env, [pay(env, "50")], op_date="2026-11-02")              # после периода
    data = money(client, env)
    rows = {r["name"]: r for r in data["rows"]}
    main = rows["Основной"]
    assert (main["opening"], main["inflow"], main["outflow"], main["closing"]) == ("1000.00", "500.00", "500.00", "1000.00")
    extra = rows["Дополнительный"]
    assert (extra["opening"], extra["inflow"], extra["closing"]) == ("0.00", "200.00", "200.00")
    assert data["closing"] == "1200.00" and data["inflow"] == "700.00" and data["outflow"] == "500.00"


def test_cash_by_cfo(client, env):
    cash_in = {"debit_account_id": env["acc"]["50"], "credit_account_id": env["acc"]["51"], "amount": "100",
               "debit": {"cfo": env["shop"]["id"], "cash_flow_item": env["art"]["Прочие поступления"]},
               "credit": {"bank_account": env["main"]["id"], "cash_flow_item": env["art"]["Прочие выплаты"]}}
    op(client, env, [cash_in])
    rows = {r["name"]: r for r in money(client, env)["rows"]}
    assert rows["Касса — Магазин"]["closing"] == "100.00" and rows["Касса — Магазин"]["kind"] == "cash"
    assert rows["Основной"]["closing"] == "-100.00"


def test_movements_with_running_balance(client, env):
    op(client, env, [receive(env, "1000")], op_date="2026-09-20")
    first = op(client, env, [receive(env, "500")], op_date="2026-10-03")
    op(client, env, [pay(env, "300")], op_date="2026-10-04")
    op(client, env, [transfer(env, "200")], op_date="2026-10-05")
    resp = client.get(f"{env['o']}/money/movements", headers=env["h"], params={
        "bank_account_id": env["main"]["id"], "date_from": "2026-10-01", "date_to": "2026-10-31"})
    data = resp.json()
    assert data["opening"] == "1000.00" and data["closing"] == "1000.00"
    rows = data["rows"]
    assert [(r["inflow"], r["outflow"], r["balance"]) for r in rows] == [
        ("500.00", None, "1500.00"), (None, "300.00", "1200.00"), (None, "200.00", "1000.00")]
    assert rows[0]["operation_id"] == first["id"]
    assert rows[1]["counterparty_id"] == env["cp"]["id"]  # контрагент — с другой стороны проводки
    assert rows[1]["cash_flow_item"] == env["art"]["Оплата поставщикам"]


def test_money_rights_and_validation(client, env):
    assert money(client, env, h=env["headers_for"](env["ceo"]))["rows"]  # CEO смотрит
    h_other = env["headers_for"](env["other_fd"])
    assert client.get(f"{env['o']}/money", headers=h_other,
                      params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).status_code == 404
    assert client.get(f"{env['o']}/money", headers=env["h"],
                      params={"date_from": "2026-10-31", "date_to": "2026-10-01"}).status_code == 422
    assert client.get(f"{env['o']}/money/movements", headers=env["h"],
                      params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).status_code == 422


# --- Начальные остатки ---


def set_opening_date(client, env, value):
    return client.patch(env["o"], headers=env["h"], json={"opening_balance_date": value})


def opening(env, amount, dt="51", ct="00", **analytics):
    return {"debit_account_id": env["acc"][dt], "credit_account_id": env["acc"][ct], "amount": amount, **analytics}


def test_opening_balances_flow(client, env):
    # без даты остатков ввод невозможен
    resp = client.post(f"{env['o']}/operations", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2025-12-31", "is_opening": True})
    assert resp.status_code == 422 and "дату начальных остатков" in resp.json()["detail"]

    assert set_opening_date(client, env, "2025-12-31").status_code == 200
    money_in = opening(env, "1000", dt="51", ct="00",
                       debit={"bank_account": env["main"]["id"], "cash_flow_item": env["art"]["Прочие поступления"]})
    debt = opening(env, "400", dt="00", ct="60.01",
                   credit={"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]})
    created = op(client, env, [money_in, debt], op_date="2026-05-05", is_opening=True)
    assert created["is_opening"] and created["op_date"] == "2025-12-31"  # дата — дата остатков

    summary = client.get(f"{env['o']}/opening-balances", headers=env["h"]).json()
    assert summary == {"opening_balance_date": "2025-12-31", "account_00_balance": "-600.00", "posted": 1, "drafts": 0}

    capital = opening(env, "600", dt="00", ct="80")
    op(client, env, [capital], is_opening=True)
    assert client.get(f"{env['o']}/opening-balances", headers=env["h"]).json()["account_00_balance"] == "0.00"

    # остаток на счёте виден в «Деньгах» как остаток на начало
    rows = {r["name"]: r for r in money(client, env, "2026-01-01", "2026-01-31")["rows"]}
    assert rows["Основной"]["opening"] == "1000.00"

    # дату больше менять нельзя
    assert set_opening_date(client, env, "2026-01-31").status_code == 409

    # в журнале по умолчанию — все; с opening=false — без ввода остатков
    listed = client.get(f"{env['o']}/operations", headers=env["h"], params={"opening": "false"}).json()
    assert listed["total"] == 0


def test_opening_posting_must_use_account_00(client, env):
    set_opening_date(client, env, "2025-12-31")
    bad = op(client, env, [receive(env, "100")], is_opening=True, post=False)
    resp = client.post(f"/api/operations/{bad['id']}/post", headers=env["h"])
    assert resp.status_code == 422 and "одна из сторон — счёт 00" in resp.json()["detail"]


def test_regular_operation_rules(client, env):
    set_opening_date(client, env, "2025-12-31")
    early = op(client, env, [receive(env, "100")], op_date="2025-12-31", post=False)
    detail = client.post(f"/api/operations/{early['id']}/post", headers=env["h"]).json()["detail"]
    assert "позже даты начальных остатков (31.12.2025)" in detail

    with_00 = op(client, env, [opening(env, "100", debit={"bank_account": env["main"]["id"],
                                                             "cash_flow_item": env["art"]["Прочие поступления"]})],
                 op_date="2026-01-10", post=False)
    detail = client.post(f"/api/operations/{with_00['id']}/post", headers=env["h"]).json()["detail"]
    assert "счёт 00 используется только для ввода начальных остатков" in detail


def test_ceo_cannot_set_opening_date(client, env):
    resp = client.patch(env["o"], headers=env["headers_for"](env["ceo"]), json={"opening_balance_date": "2025-12-31"})
    assert resp.status_code == 403


def test_transfer_article_seeded(client, env):
    arts = {a["name"]: a for a in client.get("/api/articles", headers=env["h"]).json()}
    t = arts["Перевод между своими счетами"]
    assert t["is_transfer"] and t["is_cash_flow"]
    assert t["debit_account_id"] == t["credit_account_id"] == env["acc"]["51"]
