"""Касса на «Деньгах»: добавить кассу ЦФО, сдача в банк, выдача под отчёт (docs/TZ.md, раздел 17, «Детали 7.4»)."""

from tests.test_money_page import env, money, op  # noqa: F401


def rows(client, env):  # noqa: F811
    return {r["name"]: r for r in money(client, env, "2026-10-01", "2026-10-31")["rows"]}


def test_cash_desk(client, env):  # noqa: F811
    assert "Касса — Магазин" not in rows(client, env)
    resp = client.post(f"{env['o']}/cash-desks", headers=env["h"], json={"cfo_id": env["shop"]["id"]})
    assert resp.status_code == 204, resp.text
    cash = rows(client, env)["Касса — Магазин"]
    assert (cash["kind"], cash["cfo_id"], cash["closing"]) == ("cash", env["shop"]["id"], "0.00")

    acc, art = env["acc"], env["art"]
    transfer = art["Перевод между своими счетами"]
    cash_side = {"cfo": env["shop"]["id"]}
    # поступление в кассу, сдача в банк, снятие с расчётного счёта, выдача под отчёт
    employee = client.post(f"{env['o']}/employees", headers=env["h"], json={"full_name": "Кассир"}).json()
    op(client, env, [{"debit_account_id": acc["50"], "credit_account_id": acc["60.01"], "amount": "5000",
                      "debit": {**cash_side, "cash_flow_item": art["Возврат средств от поставщиков"]},
                      "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}])
    op(client, env, [{"debit_account_id": acc["51"], "credit_account_id": acc["50"], "amount": "3000",
                      "debit": {"bank_account": env["main"]["id"], "cash_flow_item": transfer},
                      "credit": {**cash_side, "cash_flow_item": transfer}}], article_id=transfer)
    op(client, env, [{"debit_account_id": acc["50"], "credit_account_id": acc["51"], "amount": "500",
                      "debit": {**cash_side, "cash_flow_item": transfer},
                      "credit": {"bank_account": env["main"]["id"], "cash_flow_item": transfer}}], article_id=transfer)
    op(client, env, [{"debit_account_id": acc["71"], "credit_account_id": acc["50"], "amount": "1000",
                      "debit": {"employee": employee["id"]},
                      "credit": {**cash_side, "cash_flow_item": art["Выдача под отчёт"]}}])
    data = rows(client, env)
    assert (data["Касса — Магазин"]["inflow"], data["Касса — Магазин"]["outflow"], data["Касса — Магазин"]["closing"]) == (
        "5500.00", "4000.00", "1500.00")
    assert data["Основной"]["closing"] == "2500.00"

    # кассу с движением убрать из списка нельзя — она видна всегда
    assert client.delete(f"{env['o']}/cash-desks/{env['shop']['id']}", headers=env["h"]).status_code == 204
    assert "Касса — Магазин" in rows(client, env)


def test_cash_desk_access(client, env):  # noqa: F811
    from app.models import User

    ceo = env["headers_for"](env["ceo"]) if isinstance(env["ceo"], User) else env["ceo"]
    assert client.post(f"{env['o']}/cash-desks", headers=ceo, json={"cfo_id": env["shop"]["id"]}).status_code == 403
    assert client.post(f"{env['o']}/cash-desks", headers=env["h"], json={"cfo_id": 999999}).status_code == 404
    # пустая касса убирается
    client.post(f"{env['o']}/cash-desks", headers=env["h"], json={"cfo_id": env["shop"]["id"]})
    client.delete(f"{env['o']}/cash-desks/{env['shop']['id']}", headers=env["h"])
    assert "Касса — Магазин" not in rows(client, env)
