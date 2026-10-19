"""ОПиУ: разделы, итоги, перенос затрат, налог на прибыль, расшифровка, Excel (docs/TZ.md, раздел 12)."""

import io

from openpyxl import load_workbook

from tests.test_money_page import env, op  # noqa: F401

PERIOD = {"date_from": "2026-10-01", "date_to": "2026-11-30"}


def p(env, dt, ct, amount, article, debit=None, credit=None):
    return {"debit_account_id": env["acc"][dt], "credit_account_id": env["acc"][ct], "amount": amount,
            "debit": debit or {}, "credit": credit or {}}


def cost_side(env, article):
    return {"cfo": env["shop"]["id"], "pl_item": env["art"][article]}


def settle(env):
    return {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}


def month_data(client, env):
    sale, rent, it = "Выручка от продаж", "Аренда", "Связь и IT"
    op(client, env, [p(env, "62.01", "90.01", "12000", sale, settle(env), cost_side(env, sale)),
                     p(env, "90.03", "68.02", "2000", sale, cost_side(env, sale))], op_date="2026-10-05")
    op(client, env, [p(env, "26", "60.01", "1000", it, cost_side(env, it), settle(env))], op_date="2026-10-10")
    op(client, env, [p(env, "44", "60.01", "3000", rent, cost_side(env, rent), settle(env))], op_date="2026-10-20")
    # перенос затрат: Дт 90.02 — Кт 26 по статье «Себестоимость продаж» гасится внутри статьи
    op(client, env, [p(env, "90.02", "26", "1000", None, cost_side(env, "Себестоимость продаж"),
                       cost_side(env, "Себестоимость продаж"))], op_date="2026-10-31")
    fee = env["art"]["Банковские комиссии"]
    op(client, env, [p(env, "91.02", "51", "100", None, cost_side(env, "Банковские комиссии"),
                       {"bank_account": env["main"]["id"], "cash_flow_item": fee})], op_date="2026-11-03")
    op(client, env, [p(env, "99", "68.04", "500", None, {"pl_item": env["art"]["Налог на прибыль"]})],
       op_date="2026-11-30")
    op(client, env, [p(env, "44", "60.01", "700", rent, cost_side(env, rent), settle(env))],
       op_date="2026-11-15", post=False)


def pl(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/reports/pl", headers=h or env["h"], params={**PERIOD, **params})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["by_key"] = {r["key"]: r for r in data["rows"]}
    return data


def values(row):
    return [row["values"]["2026-10"], row["values"]["2026-11"], row["values"]["total"]]


def test_pl_sections_and_totals(client, env):  # noqa: F811
    month_data(client, env)
    rows = pl(client, env)["by_key"]
    assert values(rows["revenue"]) == ["10000.00", "0.00", "10000.00"]  # выручка за вычетом НДС
    assert values(rows[f"art:{env['art']['Выручка от продаж']}"]) == ["10000.00", "0.00", "10000.00"]
    assert values(rows["cost"]) == ["0.00", "0.00", "0.00"]
    assert f"art:{env['art']['Себестоимость продаж']}" not in rows
    assert values(rows["gross"]) == ["10000.00", "0.00", "10000.00"]
    assert values(rows["selling"]) == ["-3000.00", "0.00", "-3000.00"]
    assert values(rows["admin"]) == ["-1000.00", "0.00", "-1000.00"]
    assert values(rows["operating"]) == ["6000.00", "0.00", "6000.00"]
    assert values(rows["other_expense"]) == ["0.00", "-100.00", "-100.00"]
    assert values(rows["before_tax"]) == ["6000.00", "-100.00", "5900.00"]
    assert values(rows["income_tax"]) == ["0.00", "-500.00", "-500.00"]
    assert values(rows["net"]) == ["6000.00", "-600.00", "5400.00"]
    assert [r["key"] for r in pl(client, env)["rows"] if r["level"] == 0] == [
        "revenue", "cost", "gross", "selling", "admin", "operating", "other_income", "other_expense",
        "before_tax", "income_tax", "net"]

    with_drafts = pl(client, env, drafts=True)["by_key"]
    assert values(with_drafts["selling"])[1] == "-700.00"
    # налог на прибыль — без ЦФО, при фильтре по ЦФО не виден
    by_cfo = pl(client, env, cfo_id=env["shop"]["id"])["by_key"]
    assert values(by_cfo["net"])[2] == "5900.00"


def test_pl_drill_matches_cell(client, env):  # noqa: F811
    month_data(client, env)
    row = pl(client, env)["by_key"][f"art:{env['art']['Выручка от продаж']}"]
    resp = client.get(f"{env['o']}/reports/lines", headers=env["h"], params={
        "date_from": "2026-10-01", "date_to": "2026-10-31", "period": row["drill"]["period"], "a": row["drill"]["a"]})
    lines = resp.json()
    assert (lines["credit"], lines["debit"]) == ("12000.00", "2000.00")  # кредит − дебет = 10 000


def test_tax_requires_pl_item_on_99(client, env):  # noqa: F811
    body = {"legal_entity_id": env["le"].id, "op_date": "2026-11-30",
            "postings": [p(env, "99", "68.04", "500", None)]}
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json=body)
    assert resp.status_code == 422 and "Статья ОПиУ" in resp.text


def test_pl_xlsx_and_access(client, env):  # noqa: F811
    month_data(client, env)
    resp = client.get(f"{env['o']}/reports/pl.xlsx", headers=env["h"], params=PERIOD)
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    table = {row[0]: row[1:] for row in ws.iter_rows(min_row=4, values_only=True)}
    assert table["Чистая прибыль"] == (6000, -600, 5400)
    assert table["Аренда"] == (-3000, 0, -3000)  # ноль в Excel показывается пустой ячейкой

    ceo = env["headers_for"](env["ceo"])
    assert pl(client, env, h=ceo)["by_key"]["net"]["values"]["total"] == "5400.00"
    other = env["headers_for"](env["other_fd"])
    assert client.get(f"{env['o']}/reports/pl", headers=other, params=PERIOD).status_code == 404
