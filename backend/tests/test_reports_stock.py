"""Склад: остатки и обороты по складам и номенклатуре (docs/TZ.md, раздел 15, «Детали 5.3»)."""

import pytest

from tests.test_closing import env  # noqa: F401

OCT = {"date_from": "2026-10-01", "date_to": "2026-10-31"}


@pytest.fixture
def stock_env(client, env):  # noqa: F811
    post = env["post"]
    unit = next(u for u in client.get("/api/units", headers=env["h"]).json() if u["short_name"] == "шт")
    env["w1"] = post("warehouses", {"name": "Основной склад"})
    env["w2"] = post("warehouses", {"name": "Магазин"})
    env["item"] = post("nomenclature", {"name": "Чайник", "unit_id": unit["id"], "kind": "goods"})
    acc = env["acc"]

    def goods(warehouse):
        return {"nomenclature": env["item"]["id"], "warehouse": env[warehouse]["id"]}

    supplier = {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}
    cost = {"cfo": env["office"]["id"], "pl_item": env["art"]["Себестоимость продаж"]}
    for op_date, postings in [
        ("2026-09-20", [(acc["41"], acc["60.01"], "1000", "10", goods("w1"), supplier)]),
        ("2026-10-05", [(acc["41"], acc["60.01"], "600", "5", goods("w1"), supplier)]),
        ("2026-10-10", [(acc["41"], acc["41"], "300", "3", goods("w2"), goods("w1"))]),  # перемещение
        ("2026-10-15", [(acc["90.02"], acc["41"], "200", "2", cost, goods("w2"))]),  # списание проданного
    ]:
        resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
            "legal_entity_id": env["le"].id, "op_date": op_date, "postings": [
                {"debit_account_id": d, "credit_account_id": c, "amount": a, "quantity": q, "debit": da, "credit": ca}
                for d, c, a, q, da, ca in postings]})
        assert resp.status_code == 201, resp.text
    return env


def get(client, env, **params):
    resp = client.get(f"{env['o']}/reports/stock", headers=env["h"], params={**OCT, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def values(row):
    return [row[k] for k in ("opening_qty", "opening_sum", "in_qty", "in_sum", "out_qty", "out_sum",
                             "closing_qty", "closing_sum")]


def test_stock(client, stock_env):
    env = stock_env
    data = get(client, env)
    rows = {r["title"]: r for r in data["rows"]}
    assert [r["title"] for r in data["rows"]] == ["Магазин", "Чайник", "Основной склад", "Чайник"]
    main = next(r for r in data["rows"] if r["parent"] == f"wh:{env['w1']['id']}")
    assert (main["account"], main["unit"]) == ("41", "шт")
    assert values(main) == ["10.000", "1000.00", "5.000", "600.00", "3.000", "300.00", "12.000", "1300.00"]
    assert values(rows["Магазин"]) == ["0.000", "0.00", "3.000", "300.00", "2.000", "200.00", "1.000", "100.00"]
    assert [data["total"][k] for k in ("opening_qty", "in_qty", "out_qty", "closing_qty", "closing_sum")] == [
        "10.000", "8.000", "5.000", "13.000", "1400.00"]
    assert [a["code"] for a in data["accounts"]] == ["10", "41", "43"]

    # расшифровка прихода на основной склад
    lines = client.get(f"{env['o']}/reports/lines", headers=env["h"], params={
        **OCT, "period": "turnover", "side": "debit", "account_id": main["drill"]["account_id"],
        "a": main["drill"]["a"]}).json()
    assert (lines["count"], lines["debit"]) == (1, "600.00")

    only_shop = get(client, env, warehouse_id=env["w2"]["id"])
    assert [r["title"] for r in only_shop["rows"]] == ["Магазин", "Чайник"]
    assert get(client, env, account_id=env["acc"]["10"])["rows"] == []
    bad = client.get(f"{env['o']}/reports/stock", headers=env["h"], params={**OCT, "account_id": env["acc"]["51"]})
    assert bad.status_code == 422
    resp = client.get(f"{env['o']}/reports/stock.xlsx", headers=env["h"], params=OCT)
    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert client.get(f"{env['o']}/reports/stock", headers=env["other"], params=OCT).status_code == 404
