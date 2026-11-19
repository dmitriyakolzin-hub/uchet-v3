"""План объёмов и расчёт плана ОПиУ по цепочке, шаг 20.6 (docs/TZ.md, раздел 29, «Детали 20.6»)."""

import io
from decimal import Decimal

from openpyxl import load_workbook

from tests.test_closing import env  # noqa: F401


def test_volumes_chain(client, env, db):  # noqa: F811
    from app.models import Membership, User

    post, h, o = env["post"], env["h"], env["o"]
    unit = client.get("/api/units", headers=h).json()[0]["id"]
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    bakery = post("cfos", {"name": "Пекарня", "parent_id": company["id"], "chain_link": "production"})
    logistics = post("cfos", {"name": "Логистика", "parent_id": company["id"], "chain_link": "logistics"})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"], "chain_link": "trade"})
    shop = post("cfos", {"name": "Магазин 1", "parent_id": trade["id"]})
    disc = post("cfos", {"name": "Дисконт", "parent_id": company["id"], "chain_link": "discount"})
    head = db.query(User).filter_by(login="head").one()
    db.query(Membership).filter_by(user_id=head.id).one().cfo_id = trade["id"]
    db.commit()

    def item(name, route, **fields):
        return post("nomenclature", {"name": name, "unit_id": unit, "kind": "product", "route": route, **fields})

    cup = item("Стакан", "purchased")
    cake = item("Торт", "product", producer_cfo_id=bakery["id"])
    dough = item("Тесто", "product", producer_cfo_id=bakery["id"])
    croissant = item("Круассан", "assembled")
    tea = item("Чай", "purchased")
    client.put(f"/api/nomenclature/{croissant['id']}/components", headers=h,
               json=[{"component_id": dough["id"], "quantity": "0.5"}])
    for it, values in ((cup, {"logistics": "10", "sale": "30"}),
                       (cake, {"production": "100", "logistics": "150", "sale": "300", "discount": "200"}),
                       (dough, {"production": "20", "logistics": "30"}), (croissant, {"sale": "120"})):
        resp = client.post(f"{o}/nomenclature-prices", headers=h, json={"nomenclature_id": it["id"],
                                                                         "valid_from": "2020-01-01", **values})
        assert resp.status_code == 201, resp.text
    budget = post("budgets", {"kind": "year", "year": 2027})
    version = budget["versions"][0]
    url = f"/api/budget-versions/{version['id']}/volumes"
    art = {a["name"]: a["id"] for a in client.get(f"{o}/articles", headers=h).json()}

    def cell(cfo, it, kind, qty, month=3, discount=None):
        return {"cfo_id": cfo["id"], "nomenclature_id": it["id"], "kind": kind, "month": month, "quantity": qty,
                "discount_cfo_id": discount["id"] if discount else None}

    assert client.put(url, headers=env["ceo"], json={"cells": [cell(shop, cake, "sale", "10")]}).status_code == 403
    bad = client.put(url, headers=h, json={"cells": [cell(bakery, cake, "sale", "10")]})
    assert bad.status_code == 422 and "не точка продаж" in bad.json()["detail"]
    assert client.put(url, headers=h, json={"cells": [cell(shop, cake, "discount", "2")]}).status_code == 422
    resp = client.put(url, headers=h, json={"cells": [
        cell(shop, cake, "sale", "10"), cell(shop, cake, "discount", "2", discount=disc), cell(shop, cake, "waste", "1"),
        cell(shop, cup, "sale", "5"), cell(shop, croissant, "sale", "4"), cell(disc, cake, "sale", "2")]})
    assert resp.status_code == 200, resp.text
    data = client.get(url, headers=h).json()
    assert len(data["rows"]) == 6 and data["editable"]
    # руководитель торговли видит и вводит только свою ветку, но рассчитывать не может
    assert {r["cfo_id"] for r in client.get(url, headers=env["head"]).json()["rows"]} == {shop["id"]}
    assert client.put(url, headers=env["head"], json={"cells": [cell(disc, cake, "sale", "1")]}).status_code == 422
    calc = f"{url}/calculate"
    revenue = art["Выручка от продаж"]
    assert client.post(calc, headers=env["head"], json={"revenue_article_id": revenue}).status_code == 403
    assert client.post(calc, headers=h, json={"revenue_article_id": art["Аренда"]}).status_code == 422

    result = client.post(calc, headers=h, json={"revenue_article_id": revenue}).json()
    assert result["missing"] == [] and Decimal(result["total_revenue"]) == 4230  # 3000 + 150 + 480 + 600 дисконта

    def auto(cfo):
        rows = client.get(f"/api/budget-versions/{version['id']}/grid", headers=h,
                          params={"kind": "pl", "cfo_id": cfo["id"]}).json()["rows"]
        return {r["name"].split(" (")[0]: Decimal(r["values"]["2027-03"]) for r in rows if r.get("auto")}

    assert auto(shop) == {"Выручка от продаж": 3630, "Внутренняя выручка": 400, "Внутренняя закупка": 2060}
    assert auto(disc) == {"Выручка от продаж": 600, "Внутренняя закупка": 400}
    assert auto(logistics) == {"Внутренняя выручка": 2060, "Внутренняя закупка": 1340}  # торт 1300 + тесто 40
    assert auto(bakery) == {"Внутренняя выручка": 1340}

    # ручная ячейка рядом с посчитанной; посчитанную руками не поправить через окно плановых операций
    resp = client.put(f"/api/budget-versions/{version['id']}/cells", headers=h, json={
        "kind": "pl", "cfo_id": shop["id"], "cells": [{"article_id": revenue, "month": 3, "amount": "70"}]})
    assert resp.status_code == 200
    rows = client.get(f"/api/budget-versions/{version['id']}/grid", headers=h,
                      params={"kind": "pl", "cfo_id": shop["id"]}).json()["rows"]
    manual = next(r for r in rows if r["article_id"] == revenue and not r.get("auto"))
    assert Decimal(manual["values"]["2027-03"]) == 70
    cells = client.get(f"/api/budget-versions/{version['id']}/cells", headers=h, params={
        "kind": "pl", "article_id": revenue, "month": "2027-03", "cfo_id": shop["id"]}).json()["cells"]
    assert sorted((Decimal(c["amount"]), c["editable"]) for c in cells) == [(70, True), (3630, False)]

    # нет цены — ничего не меняется, список недостающих
    client.put(url, headers=h, json={"cells": [cell(shop, tea, "sale", "1")]})
    result = client.post(calc, headers=h, json={"revenue_article_id": revenue}).json()
    assert len(result["missing"]) == 2 and all("Чай" in m for m in result["missing"])
    assert auto(shop)["Выручка от продаж"] == 3630
    # объём убрали — посчитанные ячейки пересчитываются, лишние очищаются
    client.put(url, headers=h, json={"cells": [cell(shop, tea, "sale", None), cell(shop, croissant, "sale", "0"),
                                               cell(disc, cake, "sale", None)]})
    client.post(calc, headers=h, json={"revenue_article_id": revenue})
    assert auto(bakery) == {"Внутренняя выручка": 1300}
    assert auto(disc) == {"Внутренняя закупка": 400}

    # Excel: выгрузка и загрузка (пустая ячейка не меняет, 0 — удаляет)
    resp = client.get(f"{url}.xlsx", headers=h)
    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb.worksheets[0]
    lines = {(ws.cell(r, 3).value, ws.cell(r, 4).value): r for r in range(2, 8) if ws.cell(r, 3).value}
    ws.cell(lines[("Торт", "продажа")], 8).value = 12  # март — 8-я колонка (5 колонок + январь, февраль)
    ws.cell(lines[("Стакан", "продажа")], 8).value = 0
    out = io.BytesIO()
    wb.save(out)
    resp = client.post(url, headers={**h, "Content-Type": "application/octet-stream"}, content=out.getvalue())
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == []
    rows = {(r["name"], r["kind"]): r for r in client.get(url, headers=h).json()["rows"]}
    assert Decimal(rows[("Торт", "sale")]["values"]["2027-03"]) == 12 and ("Стакан", "sale") not in rows
    # новая версия «на основе» копирует объёмы
    copy = client.post(f"/api/budgets/{budget['id']}/versions", headers=h, json={"copy_from_id": version["id"]})
    assert copy.status_code == 201, copy.text
    assert len(client.get(f"/api/budget-versions/{copy.json()['id']}/volumes", headers=h).json()["rows"]) == 3
