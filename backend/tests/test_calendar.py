"""Платёжный календарь: ожидаемые платежи из заявок, ведомостей, регламентных операций и инвестпроектов
(docs/TZ.md, раздел 18, «Детали 8.2»)."""

from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import act, create, env  # noqa: F401

MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def expected(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/payment-calendar/expected", headers=h or env["h"], params=params)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["brief"] = [(r["source"], r["date"], r["direction"], r["amount"]) for r in data["rows"]]
    return data


def test_requests_and_routines(client, env):
    approved = create(client, env, pay_date="2026-03-05", amount="30000").json()
    act(client, env, approved, "submit")
    act(client, env, approved, "approve")
    pending = create(client, env, pay_date="2026-03-07", amount="1000").json()
    act(client, env, pending, "submit")
    create(client, env, pay_date="2026-03-08", amount="5")  # черновик не учитывается
    income = create(client, env, direction="in", pay_date="2026-03-20", amount="70000",
                    article_id=env["art"]["Поступление от покупателей"]).json()
    act(client, env, income, "submit")

    # регламентная оплата аренды с расчётного счёта
    pay = env["art"]["Оплата поставщикам"]
    op = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-02-10", "article_id": pay, "postings": [{
            "debit_account_id": env["acc"]["60.01"], "credit_account_id": env["acc"]["51"], "amount": "20000",
            "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
            "credit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": pay}}]}).json()
    resp = client.post(f"/api/operations/{op['id']}/routine", headers=env["h"],
                       json={"name": "Аренда", "day": 10, "valid_from": "2026-03-01"})
    assert resp.status_code == 201, resp.text

    data = expected(client, env, **MARCH)
    assert data["brief"] == [("request", "2026-03-05", "out", "30000.00"), ("routine", "2026-03-10", "out", "20000.00"),
                             ("request", "2026-03-20", "in", "70000.00")]
    routine = data["rows"][1]
    assert (routine["title"], routine["source_name"], routine["counterparty_name"], routine["article_name"]) == \
        ("Регламентная: Аренда", "Основной ··0001", "Арендодатель", "Оплата поставщикам")
    assert all(r["overdue"] for r in data["rows"])  # даты раньше сегодняшней
    assert (data["total_in"], data["total_out"]) == ("70000.00", "50000.00")
    assert [r["source"] for r in expected(client, env, include_pending=True, **MARCH)["rows"]].count("request") == 3

    # правило выполнено за март — в календаре его больше нет, апрель остаётся
    assert client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 3}).status_code == 200
    assert [r["source"] for r in expected(client, env, **MARCH)["rows"]] == ["request", "request"]
    assert expected(client, env, date_from="2026-04-01", date_to="2026-04-30")["brief"] == \
        [("routine", "2026-04-10", "out", "20000.00")]

    # оплаченная заявка — уже факт
    act(client, env, approved, "pay", paid_on="2026-03-05")
    assert [r["direction"] for r in expected(client, env, **MARCH)["rows"]] == ["in"]

    # доступ
    url = f"{env['o']}/payment-calendar/expected"
    assert client.get(url, headers=env["ceo"], params=MARCH).status_code == 200
    assert client.get(url, headers=env["head"], params=MARCH).status_code == 403
    assert client.get(url, headers=env["other"], params=MARCH).status_code == 404
    assert client.get(url, headers=env["h"], params={"date_from": "2026-03-31", "date_to": "2026-03-01"}).status_code == 422


# --- календарь по дням (8.3) ---


def money_op(client, env, op_date, amount, inflow):
    pay = env["art"]["Возврат средств от поставщиков" if inflow else "Оплата поставщикам"]
    supplier = {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}
    bank = {"bank_account": env["bank_acc"]["id"], "cash_flow_item": pay}
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": op_date, "article_id": pay, "postings": [{
            "debit_account_id": env["acc"]["51" if inflow else "60.01"],
            "credit_account_id": env["acc"]["60.01" if inflow else "51"], "amount": amount,
            "debit": bank if inflow else supplier, "credit": supplier if inflow else bank}]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def approved_request(client, env, pay_date, amount):
    req = create(client, env, pay_date=pay_date, amount=amount, bank_account_id=env["bank_acc"]["id"]).json()
    act(client, env, req, "submit")
    return act(client, env, req, "approve").json()


def test_grid_by_days(client, env, db):
    import datetime as dt

    from app.services import calendar

    money_op(client, env, "2026-02-20", "100000", inflow=True)
    out = money_op(client, env, "2026-03-03", "30000", inflow=False)
    approved_request(client, env, "2026-03-05", "5000")  # просрочен к 10.03
    approved_request(client, env, "2026-03-12", "90000")
    org, today = env["org"].id, dt.date(2026, 3, 10)

    grid = calendar.grid(db, org, dt.date(2026, 3, 1), dt.date(2026, 3, 31), calendar.Scope(), today=today)
    days = {d.date.day: d for d in grid.days}
    assert grid.opening == 100000 and len(grid.days) == 31
    assert (days[3].fact_out, days[3].closing, days[3].fact[0].number) == (30000, 70000, out["number"])
    assert days[5].expected == []  # просроченная заявка — на сегодня
    assert (days[10].expected_out, days[10].closing, days[10].expected[0].overdue) == (5000, 65000, True)
    assert (days[12].closing, grid.first_gap.date) == (-25000, dt.date(2026, 3, 12))
    assert days[31].closing == -25000

    # будущий период: остаток сегодня плюс ожидаемое до начала периода
    april = calendar.grid(db, org, dt.date(2026, 4, 1), dt.date(2026, 4, 30), calendar.Scope(), today=today)
    assert april.opening == -25000 and april.first_gap.date == dt.date(2026, 4, 1)
    # прошедший период — только факт
    past = calendar.grid(db, org, dt.date(2026, 2, 1), dt.date(2026, 2, 28), calendar.Scope(), today=today)
    assert (past.opening, past.days[-1].closing, past.first_gap) == (0, 100000, None)

    # один расчётный счёт и касса
    bank = calendar.grid(db, org, dt.date(2026, 3, 1), dt.date(2026, 3, 31),
                         calendar.Scope(bank_account_id=env["bank_acc"]["id"]), today=today)
    assert bank.days[-1].closing == -25000
    cash = calendar.grid(db, org, dt.date(2026, 3, 1), dt.date(2026, 3, 31),
                         calendar.Scope(cash_cfo_id=env["office"]["id"]), today=today)
    assert cash.opening == 0 and cash.days[-1].closing == 0


def test_grid_api(client, env):
    money_op(client, env, "2026-02-20", "100000", inflow=True)
    url = f"{env['o']}/payment-calendar"
    params = {"date_from": "2026-02-16", "date_to": "2026-03-01"}
    resp = client.get(url, headers=env["h"], params=params)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["opening"], data["inflow"], data["closing"], data["first_gap"]) == ("0.00", "100000.00", "100000.00", None)
    assert [(w["date_from"], w["date_to"], w["inflow"]) for w in data["weeks"]] == [
        ("2026-02-16", "2026-02-22", "100000.00"), ("2026-02-23", "2026-03-01", "0.00")]
    day = next(d for d in data["days"] if d["date"] == "2026-02-20")
    assert day["fact"][0]["inflow"] == "100000.00"

    xlsx = client.get(f"{url}.xlsx", headers=env["h"], params=params)
    assert xlsx.status_code == 200
    assert client.get(url, headers=env["head"], params=params).status_code == 403
    assert client.get(url, headers=env["other"], params=params).status_code == 404
    assert client.get(url, headers=env["h"], params={"date_from": "2026-01-01", "date_to": "2026-12-31"}).status_code == 422
    assert client.get(url, headers=env["h"], params={**params, "bank_account_id": env["bank_acc"]["id"],
                                                     "cash_cfo_id": env["office"]["id"]}).status_code == 422
