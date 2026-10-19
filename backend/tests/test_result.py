"""Финансовый результат: закрытие 26, 44, 90, 91 на 99 и реформация 99 → 84 (docs/TZ.md, раздел 15, «Детали 5.1»)."""

from tests.test_closing import close, env, operation, state  # noqa: F401

YEAR = {"date_from": "2026-01-01", "date_to": "2026-12-31"}


def post_op(client, env, op_date, postings):
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": op_date, "postings": postings})
    assert resp.status_code == 201, resp.text
    return resp.json()


def revenue(env, amount):
    return {"debit_account_id": env["acc"]["62.01"], "credit_account_id": env["acc"]["90.01"], "amount": amount,
            "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
            "credit": {"cfo": env["office"]["id"], "pl_item": env["art"]["Выручка от продаж"]}}


def tax(env, amount):
    return {"debit_account_id": env["acc"]["99"], "credit_account_id": env["acc"]["68.04"], "amount": amount,
            "debit": {"pl_item": env["art"]["Налог на прибыль"]}}


def osv(client, env, **period):
    resp = client.get(f"{env['o']}/reports/osv", headers=env["h"], params=period)
    assert resp.status_code == 200, resp.text
    return {r["code"]: (r["closing_debit"], r["closing_credit"]) for r in resp.json()["rows"]}


def pl_total(client, env, **period):
    resp = client.get(f"{env['o']}/reports/pl", headers=env["h"], params=period).json()
    rows = {r["key"]: r for r in resp["rows"]}
    return rows, [r for r in resp["rows"] if r["kind"] == "total"][-1]["values"]["total"]


def run(client, env, month):
    return client.post(f"{env['o']}/closing/result", headers=env["h"], json={"year": 2026, "month": month})


def test_month_result(client, env):
    post_op(client, env, "2026-01-15", [revenue(env, "120000")])
    operation(client, env, "2026-01-10")  # аренда 50 000 на 26
    post_op(client, env, "2026-01-31", [tax(env, "14000")])
    jan = {"date_from": "2026-01-01", "date_to": "2026-01-31"}
    _, before = pl_total(client, env, **jan)
    assert before == "56000.00"

    data = state(client, env, 1)
    assert data["by_code"]["result"]["details"] == ["Не закрыты: А"] and not data["can_close"]
    assert close(client, env, 1, with_result=False).status_code == 422

    resp = run(client, env, 1)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    [closing] = data["result_operations"]
    assert closing["kind"] == "month" and closing["total"] == "220000.00"  # 50 000 (26 → 90.02) + 50 000 + 120 000

    op = client.get(f"/api/operations/{closing['operation_id']}", headers=env["h"]).json()
    rent = {"cfo": env["office"]["id"], "pl_item": env["art"]["Аренда"]}
    moves = [(p["debit_account_id"], p["credit_account_id"], p["amount"], p["debit"], p["credit"]) for p in op["postings"]]
    acc = env["acc"]
    assert moves == [
        (acc["90.02"], acc["26"], "50000.00", rent, rent),
        (acc["90.01"], acc["99"], "120000.00", {"cfo": env["office"]["id"], "pl_item": env["art"]["Выручка от продаж"]},
         {"pl_item": env["art"]["Выручка от продаж"]}),
        (acc["99"], acc["90.02"], "50000.00", {"pl_item": env["art"]["Аренда"]}, rent),
    ]
    balances = osv(client, env, **jan)
    assert balances["26"] == ("0.00", "0.00") and balances["90"] == ("0.00", "0.00")
    assert balances["99"] == ("0.00", "56000.00")  # прибыль после налога
    _, after = pl_total(client, env, **jan)
    assert after == before  # ОПиУ не меняется

    # провели ещё расход — повторное выполнение пересоздаёт операцию месяца
    operation(client, env, "2026-01-20")
    assert state(client, env, 1)["by_code"]["result"]["ok"] is False
    [again] = run(client, env, 1).json()["result_operations"]
    assert again["operation_id"] != closing["operation_id"]
    assert client.get(f"/api/operations/{closing['operation_id']}", headers=env["h"]).status_code == 404
    assert osv(client, env, **jan)["99"] == ("0.00", "6000.00")
    assert state(client, env, 1)["can_close"]
    assert close(client, env, 1, with_result=False).status_code == 200
    assert run(client, env, 1).status_code == 422  # закрытый месяц не пересчитывается


def test_december_reformation(client, env):
    post_op(client, env, "2026-03-15", [revenue(env, "100000")])
    operation(client, env, "2026-12-10")  # аренда 50 000
    _, year_before = pl_total(client, env, **YEAR)
    data = run(client, env, 3).json()
    assert [r["kind"] for r in data["result_operations"]] == ["month"]
    data = run(client, env, 12).json()
    assert sorted(r["kind"] for r in data["result_operations"]) == ["month", "year"]
    balances = osv(client, env, **YEAR)
    assert balances["99"] == ("0.00", "0.00")
    assert balances["84"] == ("0.00", "50000.00")  # нераспределённая прибыль
    rows, year_after = pl_total(client, env, **YEAR)
    assert year_after == year_before == "50000.00"  # реформация в ОПиУ не попадает

    # расшифровка статьи ОПиУ — без проводок реформации
    row = rows[f"art:{env['art']['Выручка от продаж']}"]
    lines = client.get(f"{env['o']}/reports/lines", headers=env["h"], params={
        **YEAR, "period": "turnover", "a": row["drill"]["a"],
        "exclude_corr_account_id": row["drill"]["exclude_corr_ids"]}).json()
    assert (lines["credit"], lines["debit"]) == ("200000.00", "100000.00")  # выручка и её перенос 90.01 → 99

    resp = client.delete(f"{env['o']}/closing/result", headers=env["h"], params={"year": 2026, "month": 12})
    assert resp.status_code == 200 and resp.json()["result_operations"] == []
    assert "84" not in osv(client, env, **YEAR)


def test_access(client, env):
    for h in (env["ceo"], env["head"]):
        assert client.post(f"{env['o']}/closing/result", headers=h, json={"year": 2026, "month": 1}).status_code == 403
    assert client.post(f"{env['o']}/closing/result", headers=env["other"],
                       json={"year": 2026, "month": 1}).status_code == 404
