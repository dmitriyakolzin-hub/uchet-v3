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
