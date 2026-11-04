"""Платёжный календарь по форме владельца — «лист 1»: остатки по счетам, факт и план в ячейке, статьи → контрагент
+ договор, ручные остатки, правка суммы и перенос (docs/TZ.md, раздел 24, «Детали 14.6»). Данные вымышленные."""

import datetime as dt

from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import act, create, env  # noqa: F401

TODAY = dt.date.today()


def d(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def sheet(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/payment-calendar/sheet", headers=h or env["h"],
                      params={"date_from": d(-3), "date_to": d(5), **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def operation(client, env, day, amount, inflow):
    art = env["art"]["Поступление от покупателей" if inflow else "Оплата поставщикам"]
    money = {"debit_account_id" if inflow else "credit_account_id": env["acc"]["51"]}
    other = {"credit_account_id" if inflow else "debit_account_id": env["acc"]["60.01"]}
    sides = ({"debit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": art},
              "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}} if inflow else
             {"credit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": art},
              "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}})
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": day, "postings": [{**money, **other, "amount": amount, **sides}]})
    assert resp.status_code == 201, resp.text


def approved(client, env, **fields):
    req = create(client, env, bank_account_id=env["bank_acc"]["id"], **fields).json()
    act(client, env, req, "submit")
    return act(client, env, req, "approve").json()


def test_sheet(client, env):
    operation(client, env, d(-2), "50000", True)
    operation(client, env, d(-1), "20000", False)
    req = approved(client, env, pay_date=d(2), amount="30000")
    approved(client, env, pay_date=d(-3), amount="1000")  # просрочена — на сегодня

    data = sheet(client, env)
    assert data["days"][0] == d(-3) and len(data["days"]) == 9
    bank = next(s for s in data["sources"] if s["key"] == f"bank:{env['bank_acc']['id']}")
    assert bank["title"] == "Основной ··0001"
    assert (bank["opening"][d(-1)]["fact"], bank["opening"][d(0)]["fact"]) == ("50000.00", "30000.00")
    assert bank["opening"][d(1)] == {"fact": None, "plan": "29000.00"}  # прогноз: 30 000 − 1 000
    assert (bank["closing"][d(0)], bank["closing"][d(2)]) == ("29000.00", "-1000.00")
    totals = data["totals"]["bank"]
    assert totals["inflow"][d(-2)] == {"fact": "50000.00", "plan": None}
    assert totals["outflow"][d(0)] == {"fact": None, "plan": "1000.00"}

    groups = {(g["direction"], g["title"]): g for g in data["groups"]}
    assert list(groups)[0][0] == "in"  # сначала поступления
    income = groups[("in", "Поступление от покупателей")]["lines"][0]
    assert (income["title"], income["cells"][d(-2)]["fact"]) == ("Арендодатель", "50000.00")
    pay = groups[("out", "Оплата поставщикам")]
    line = pay["lines"][0]
    assert (line["title"], line["contract_title"], line["bank"]) == ("Арендодатель", "№ 1", "51000.00")
    assert line["cells"][d(-1)]["fact"] == "20000.00" and line["cells"][d(-1)]["ops"][0]["amount"] == "20000.00"
    cell = line["cells"][d(2)]
    assert cell["plan"] == "30000.00" and cell["items"][0]["movable"] and cell["items"][0]["amount_editable"]
    assert line["cells"][d(0)]["items"][0]["overdue"] is True
    assert pay["cells"][d(2)] == {"fact": None, "plan": "30000.00"}

    # ручной остаток на завтра (по банк-клиенту) — прогноз идёт от него
    url = f"{env['o']}/calendar-balances"
    body = {"bank_account_id": env["bank_acc"]["id"], "day": d(1), "amount": "100000"}
    assert client.put(url, headers=env["h"], json=body).status_code == 204
    assert client.put(url, headers=env["h"], json={**body, "amount": "100500.50"}).status_code == 204
    bank = next(s for s in sheet(client, env)["sources"] if s["key"].startswith("bank:") and s["key"] != "bank:none")
    assert (bank["opening"][d(1)]["plan"], bank["closing"][d(2)]) == ("100500.50", "70500.50")
    assert client.put(url, headers=env["h"], json={"day": d(1), "amount": "1"}).status_code == 422
    assert client.put(url, headers=env["ceo"], json=body).status_code == 403
    assert client.put(url, headers=env["h"], json={**body, "amount": None}).status_code == 204
    bank = next(s for s in sheet(client, env)["sources"] if s["key"].startswith("bank:") and s["key"] != "bank:none")
    assert bank["opening"][d(1)]["plan"] == "29000.00"

    # правка суммы в ячейке — та же заявка, с историей; перенос
    resp = act(client, env, req, "amount", amount="35000")
    assert resp.status_code == 200 and resp.json()["status"] == "approved", resp.text
    events = client.get(f"/api/payment-requests/{req['id']}/events", headers=env["h"]).json()
    assert (events[-1]["action"], events[-1]["comment"]) == ("amount", "30000.00 → 35000.00")
    act(client, env, req, "move", pay_date=d(4), bank_account_id=env["bank_acc"]["id"])
    line = next(g for g in sheet(client, env)["groups"] if g["title"] == "Оплата поставщикам")["lines"][0]
    assert d(2) not in line["cells"] and line["cells"][d(4)]["plan"] == "35000.00"
    assert act(client, env, req, "amount", h=env["ceo"], amount="1").status_code == 403

    # Excel, права, период
    resp = client.get(f"{env['o']}/payment-calendar/sheet.xlsx", headers=env["h"], params={"date_from": d(-3), "date_to": d(5)})
    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert client.get(f"{env['o']}/payment-calendar/sheet", headers=env["head"],
                      params={"date_from": d(0), "date_to": d(1)}).status_code == 403
    assert client.get(f"{env['o']}/payment-calendar/sheet", headers=env["h"],
                      params={"date_from": d(0), "date_to": d(70)}).status_code == 422
    # будущий период: остаток на начало — прогноз
    future = sheet(client, env, date_from=d(3), date_to=d(5))
    bank = next(s for s in future["sources"] if s["key"].startswith("bank:") and s["key"] != "bank:none")
    assert bank["opening"][d(4)]["plan"] == "29000.00" and bank["closing"][d(4)] == "-6000.00"


def test_periodic_keeps_manual_move(client, env):
    month = TODAY.replace(day=1)
    resp = client.post(f"/api/contracts/{env['contract']['id']}/payments", headers=env["h"], json={
        "valid_from": month.isoformat(), "day": 28, "amount": "5000", "article_id": env["art"]["Оплата поставщикам"],
        "bank_account_id": env["bank_acc"]["id"]})
    assert resp.status_code == 201, resp.text
    rows = client.get(f"{env['o']}/periodic-payments", headers=env["h"]).json()
    req = {"id": rows[0]["months"][0]["request_id"]}
    moved = (month + dt.timedelta(days=10)).isoformat()
    assert act(client, env, req, "move", pay_date=moved, bank_account_id=env["bank_acc"]["id"]).status_code == 200
    assert act(client, env, req, "amount", amount="5500").status_code == 200
    rows = client.get(f"{env['o']}/periodic-payments", headers=env["h"]).json()  # подстройка под договор не откатывает
    m = rows[0]["months"][0]
    assert (m["pay_date"], m["amount"]) == (moved, "5500.00")
