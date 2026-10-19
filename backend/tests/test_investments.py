"""Инвестиционный бюджет: проекты, план инвестиций с плановыми оплатами, отчёт «Инвестиции» (docs/TZ.md, раздел 16)."""

import pytest

from tests.test_calendar import expected as calendar_expected
from tests.test_plan_grid import env, grid  # noqa: F401
from tests.test_users import admin  # noqa: F401

JAN = {"date_from": "2027-01-01", "date_to": "2027-01-31"}


@pytest.fixture
def inv(client, env, db):  # noqa: F811
    from app.models import Currency

    h, o = env["h"], env["o"]
    rub = db.query(Currency).filter_by(code="RUB").one()
    project = client.post(f"{o}/investment-projects", headers=h, json={
        "name": "Новый магазин", "cfo_id": env["shop"]["id"], "limit_amount": "500000", "payment_lag_months": 1,
        "status": "active"})
    assert project.status_code == 201, project.text
    cp = client.post(f"{o}/counterparties", headers=h, json={"name": "Подрядчик", "kind": "legal",
                                                             "inn": "7702222222"}).json()
    contract = client.post(f"{o}/contracts", headers=h, json={
        "counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": rub.id, "kind": "supplier",
        "number": "С-1", "project_id": project.json()["id"]})
    assert contract.status_code == 201, contract.text
    env.update(project=project.json(), cp=cp, contract=contract.json())
    return env


def cells(client, env, items, h=None):
    return client.put(f"/api/budget-versions/{env['version']['id']}/cells", headers=h or env["h"], json={
        "kind": "inv", "project_id": env["project"]["id"],
        "cells": [{"article_id": env["art"][a], "month": m, "amount": v} for a, m, v in items]})


def payments(db, env):
    from app.models import Operation

    db.expire_all()
    ops = db.query(Operation).filter_by(budget_version_id=env["version"]["id"], plan_kind="cf",
                                        plan_project_id=env["project"]["id"]).all()
    return {op.op_date.isoformat(): str(sum(p.amount for p in op.postings)) for op in ops}


def test_investment_plan_and_payments(client, inv, db):
    env = inv
    resp = cells(client, env, [("Инвестиции: оборудование", 1, "100000"), ("Инвестиции: ремонт и строительство", 1, "50000"),
                              ("Инвестиции: оборудование", 2, "30000")])
    assert resp.status_code == 200, resp.text
    data = grid(client, env, kind="inv", cfo=None).json()
    assert data["editable"]
    rows = {r["name"]: r for r in data["rows"] if r["project_id"] == env["project"]["id"]}
    assert set(rows) == {"Инвестиции: оборудование", "Инвестиции: ремонт и строительство", "Инвестиции: прочие вложения"}
    assert rows["Инвестиции: оборудование"]["values"] == {"2027-01": "100000.00", "2027-02": "30000.00"}
    assert "лимит 500 000,00 ₽" in rows["Инвестиции: оборудование"]["group"]

    # плановые оплаты — в плане ДДС со сдвигом на месяц, ручной план ДДС их не показывает
    assert payments(db, env) == {"2027-02-01": "150000.00", "2027-03-01": "30000.00"}
    buy = env["art"]["Покупка основных средств"]
    cf_rows = [r for r in grid(client, env, kind="cf", cfo=None).json()["rows"] if r["article_id"] == buy]
    assert cf_rows[0]["values"] == {}

    # сдвиг оплаты изменили — оплаты пересобраны
    resp = client.patch(f"/api/investment-projects/{env['project']['id']}", headers=env["h"], json={"payment_lag_months": 0})
    assert resp.status_code == 200, resp.text
    assert payments(db, env) == {"2027-01-01": "150000.00", "2027-02-01": "30000.00"}

    # очистка ячейки уменьшает оплату
    cells(client, env, [("Инвестиции: ремонт и строительство", 1, None)])
    assert payments(db, env) == {"2027-01-01": "100000.00", "2027-02-01": "30000.00"}

    # план ДДС в план/факте — инвестиционная деятельность
    client.post(f"/api/budget-versions/{env['version']['id']}/approve", headers=env["h"])
    pf = client.get(f"{env['o']}/reports/plan-fact", headers=env["h"], params={**JAN, "kind": "cf"}).json()
    row = next(r for r in pf["rows"] if r.get("key") == f"art:{buy}")
    assert row["values"]["total"]["plan"] == "-100000.00"

    # копия версии переносит и вложения, и оплаты
    copy = client.post(f"/api/budgets/{env['budget']['id']}/versions", headers=env["h"],
                       json={"name": "Копия", "copy_from_id": env["version"]["id"]}).json()
    rows = {r["name"]: r for r in grid(client, env, kind="inv", cfo=None, version=copy).json()["rows"]}
    assert rows["Инвестиции: оборудование"]["values"]["2027-01"] == "100000.00"


def test_investments_report(client, inv, db):
    env = inv
    cells(client, env, [("Инвестиции: оборудование", 1, "150000")])
    client.post(f"/api/budget-versions/{env['version']['id']}/approve", headers=env["h"])
    h, o = env["h"], env["o"]
    asset = client.post(f"{o}/fixed-assets", headers=h, json={
        "name": "Холодильная витрина", "legal_entity_id": env["le"].id, "cost": "80000", "useful_life_months": 60,
        "cfo_id": env["shop"]["id"], "project_id": env["project"]["id"]}).json()
    acc = {v: k for k, v in env["acc"].items()}
    supplier = {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}
    for op_date, posting in [
        ("2027-01-10", {"debit_account_id": acc["08"], "credit_account_id": acc["60.01"], "amount": "80000",
                        "debit": {"fixed_asset": asset["id"]}, "credit": supplier}),
        ("2027-01-20", {"debit_account_id": acc["60.01"], "credit_account_id": acc["50"], "amount": "60000",
                        "debit": supplier, "credit": {"cfo": env["shop"]["id"],
                                                      "cash_flow_item": env["art"]["Покупка основных средств"]}}),
    ]:
        resp = client.post(f"{o}/operations?post=true", headers=h, json={
            "legal_entity_id": env["le"].id, "op_date": op_date, "postings": [posting]})
        assert resp.status_code == 201, resp.text

    resp = client.get(f"{o}/reports/investments", headers=h, params=JAN)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    [row] = data["rows"]
    assert {k: row[k] for k in ("limit", "plan", "fact", "fact_total", "paid", "left")} == {
        "limit": "500000.00", "plan": "150000.00", "fact": "80000.00", "fact_total": "80000.00", "paid": "60000.00",
        "left": "420000.00"}
    assert list(data["plan_versions"]) == ["2027-01"]

    # расшифровка факта вложений и оплат — те же условия
    for part, account, amount in (("fact", "08", "80000.00"), ("paid", "60", "60000.00")):
        lines = client.get(f"{o}/reports/lines", headers=h, params={
            **JAN, "period": "turnover", "side": "debit", "account_id": data["accounts"][account],
            "project_id": env["project"]["id"], "project_part": part}).json()
        assert lines["debit"] == amount

    # февраль: за период ничего, с начала проекта — 80 000
    feb = client.get(f"{o}/reports/investments", headers=h,
                     params={"date_from": "2027-02-01", "date_to": "2027-02-28"}).json()["rows"][0]
    assert (feb["fact"], feb["fact_total"], feb["left"]) == ("0.00", "80000.00", "420000.00")

    assert client.get(f"{o}/reports/investments.xlsx", headers=h, params=JAN).content[:2] == b"PK"
    assert client.get(f"{o}/reports/investments", headers=env["ceo"], params=JAN).status_code == 200
    assert client.get(f"{o}/reports/investments", headers=env["head"], params=JAN).status_code == 403
    resp = cells(client, env, [("Инвестиции: оборудование", 2, "1")], h=env["head"])
    assert resp.status_code == 404


def test_project_checks(client, inv):
    env = inv
    resp = client.post(f"{env['o']}/investment-projects", headers=env["h"], json={
        "name": "X", "cfo_id": env["shop"]["id"], "start_on": "2027-05-01", "finish_on": "2027-01-01"})
    assert resp.status_code == 422
    resp = client.put(f"/api/budget-versions/{env['version']['id']}/cells", headers=env["h"], json={
        "kind": "inv", "cells": [{"article_id": env["art"]["Инвестиции: оборудование"], "month": 1, "amount": "1"}]})
    assert resp.status_code == 422 and "инвестпроект" in resp.json()["detail"]
    resp = client.put(f"/api/budget-versions/{env['version']['id']}/cells", headers=env["h"], json={
        "kind": "inv", "project_id": env["project"]["id"],
        "cells": [{"article_id": env["art"]["Аренда"], "month": 1, "amount": "1"}]})
    assert resp.status_code == 422


def test_investment_payments(client, inv):
    env = inv
    assert cells(client, env, [("Инвестиции: оборудование", 1, "100000")]).status_code == 200
    feb = {"date_from": "2027-02-01", "date_to": "2027-02-28"}
    assert calendar_expected(client, env, **feb)["rows"] == []  # версия ещё не утверждена
    assert client.post(f"/api/budget-versions/{env['version']['id']}/approve", headers=env["h"]).status_code == 200
    data = calendar_expected(client, env, **feb)
    assert data["brief"] == [("investment", "2027-02-01", "out", "100000.00")]
    assert data["rows"][0]["title"] == "Инвестпроект: Новый магазин"
    # период начинается позже 1-го числа — оплата на первый день периода
    assert calendar_expected(client, env, date_from="2027-02-10", date_to="2027-02-28")["brief"][0][1] == "2027-02-10"

    # заявка по договору проекта уменьшает плановую оплату месяца
    resp = client.post(f"{env['o']}/payment-requests", headers=env["h"], json={
        "pay_date": "2027-02-12", "legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"],
        "contract_id": env["contract"]["id"], "amount": "40000"})
    assert resp.status_code == 201, resp.text
    client.post(f"/api/payment-requests/{resp.json()['id']}/submit", headers=env["h"])
    assert calendar_expected(client, env, **feb)["brief"] == [("investment", "2027-02-01", "out", "60000.00")]
    assert calendar_expected(client, env, include_pending=True, **feb)["brief"] == [
        ("investment", "2027-02-01", "out", "60000.00"), ("request", "2027-02-12", "out", "40000.00")]
