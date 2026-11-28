"""Нормативы «на каждую точку торговли», шаг 24.6 (docs/TZ.md, раздел 32, «Детали 24.6»). Данные вымышленные."""

from tests.test_plan_grid import env, put  # noqa: F401
from tests.test_users import admin  # noqa: F401


def test_point_norms(client, env, db):  # noqa: F811
    from app.models import Cfo, ReportFormRow

    o, h, art, version = env["o"], env["h"], env["art"], env["version"]["id"]
    sales, rent, it = art["Выручка от продаж"], art["Аренда"], art["Связь и IT"]
    db.get(Cfo, env["trade"]["id"]).chain_link = "trade"
    for code, name, article_id in (("1.0", "Выручка", sales), ("2.1", "Аренда", rent), ("2.2", "Связь", it)):
        db.add(ReportFormRow(organization_id=env["org"].id, position=int(code[-1]) * 10 + len(code), code=code,
                             name=name, kind="article", article_id=article_id))
    db.commit()
    shop2 = client.post(f"{o}/cfos", headers=h, json={"name": "Магазин 2", "parent_id": env["trade"]["id"]}).json()
    assert put(client, env, [{"article_id": sales, "month": 3, "amount": "500000"}], cfo="shop").status_code == 200
    assert client.put(f"/api/budget-versions/{version}/cells", headers=h, json={
        "kind": "pl", "cfo_id": shop2["id"], "cells": [{"article_id": sales, "month": 3, "amount": "200000"}]}
    ).status_code == 200
    for article_id, method in ((rent, "calc"), (it, "fact")):
        client.put(f"/api/articles/{article_id}/plan-method", headers=h, json={"plan_method": method})

    def norm(**body):
        resp = client.post(f"{o}/plan-norms", headers=h, json={"valid_from": "2027-01-01"} | body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    # аренда — 3 % от выручки каждой точки «Торговли», у «Магазина 2» своя ставка 5 %
    assert norm(article_id=rent, kind="calc", cfo_id=env["trade"]["id"], base_article_id=sales, percent="3",
                per_point=True)["per_point"] is True
    norm(article_id=rent, kind="calc", cfo_id=shop2["id"], base_article_id=sales, percent="5")
    # связь — 5 000 на каждую точку всей организации (логистика — не точка)
    norm(article_id=it, kind="fact", amount="5000", per_point=True)

    def rows(cfo_id=None):
        params = {"date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version}
        data = client.get(f"{o}/reports/method-plan", headers=h,
                          params=params | ({"cfo_id": cfo_id} if cfo_id else {})).json()
        return {r["code"]: r for r in data["rows"]}

    by = rows()
    assert by["2.1"]["values"]["calc"] == "25000.00"  # 3 % × 500 000 + 5 % × 200 000
    assert {x["cfo"]: x["amount"] for x in by["2.1"]["breakdown"]["calc"]} == \
        {"Магазин 1": "15000.00", "Магазин 2": "10000.00"}
    assert "по точкам торговли: 2" in by["2.1"]["notes"]["calc"]
    assert (by["2.1"]["values"]["itog"], by["2.1"]["itog_auto"]) == ("25000.00", True)
    assert by["2.2"]["values"]["fact"] == "10000.00"
    # отчёт по одной точке — только её доля
    one = rows(env["shop"]["id"])
    assert (one["2.1"]["values"]["calc"], one["2.2"]["values"]["fact"]) == ("15000.00", "5000.00")

    # фиксация: расчётная аренда пишется в бюджет каждой точке
    assert client.post(f"/api/budget-versions/{version}/itog/fix", headers=h, json={"month": "2027-03"}).status_code == 200
    cells = client.get(f"/api/budget-versions/{version}/cells", headers=h, params={
        "kind": "pl", "article_id": rent, "month": "2027-03"}).json()["cells"]
    assert sorted((c["cfo_id"], c["amount"]) for c in cells) == \
        sorted([(env["shop"]["id"], "15000.00"), (shop2["id"], "10000.00")])
