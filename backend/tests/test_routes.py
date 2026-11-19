"""Маршрут товара до точки продаж, производство и состав доготавливаемого товара, шаг 20.5
(docs/TZ.md, раздел 29, «Детали 20.5»)."""

import io

from openpyxl import load_workbook

from tests.test_closing import env  # noqa: F401


def test_routes(client, env):  # noqa: F811
    post, h, o = env["post"], env["h"], env["o"]
    unit = client.get("/api/units", headers=h).json()[0]["id"]
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    bakery = post("cfos", {"name": "Пекарня", "parent_id": company["id"], "chain_link": "production"})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"], "chain_link": "trade"})

    def item(name, article, **fields):
        resp = client.post(f"{o}/nomenclature", headers=h, json={"name": name, "article": article, "unit_id": unit,
                                                                  "kind": "product", **fields})
        return resp

    cup = item("Стакан", "C-1", route="purchased").json()
    assert cup["route"] == "purchased" and cup["producer_cfo_id"] is None
    assert item("Торт", "T-1", route="product").status_code == 422  # нет производства
    assert "не производство" in item("Торт", "T-1", route="product", producer_cfo_id=trade["id"]).json()["detail"]
    cake = item("Торт", "T-1", route="product", producer_cfo_id=bakery["id"]).json()
    dough = item("Тесто для круассана", "D-1", route="product", producer_cfo_id=bakery["id"]).json()
    croissant = item("Круассан", "K-1", route="assembled").json()
    # производство у покупного не остаётся
    resp = client.patch(f"/api/nomenclature/{cup['id']}", headers=h, json={"producer_cfo_id": bakery["id"]})
    assert resp.status_code == 200 and resp.json()["producer_cfo_id"] is None

    url = f"/api/nomenclature/{croissant['id']}/components"
    assert client.put(url, headers=env["ceo"], json=[]).status_code == 403
    assert client.put(f"/api/nomenclature/{cake['id']}/components", headers=h,
                      json=[{"component_id": dough["id"], "quantity": "1"}]).status_code == 422  # не доготовка
    assert client.put(url, headers=h, json=[{"component_id": croissant["id"], "quantity": "1"}]).status_code == 422
    resp = client.put(url, headers=h, json=[{"component_id": dough["id"], "quantity": "0.08"},
                                            {"component_id": cup["id"], "quantity": "1"}])
    assert resp.status_code == 200, resp.text
    assert [(c["name"], c["quantity"]) for c in client.get(url, headers=env["ceo"]).json()] == [
        ("Тесто для круассана", "0.0800"), ("Стакан", "1.0000")]
    # смена маршрута убирает состав; звено ЦФО можно вернуть «как у вышестоящего»
    client.patch(f"/api/nomenclature/{croissant['id']}", headers=h, json={"route": "purchased"})
    assert client.get(url, headers=h).json() == []
    resp = client.patch(f"/api/cfos/{trade['id']}", headers=h, json={"chain_link": None})
    assert resp.status_code == 200 and resp.json()["chain_link"] is None

    # Excel: выгрузка в формате загрузки, загрузка маршрута, производства и состава (по строке на составляющую)
    resp = client.get(f"{o}/import/routes.xlsx", headers=h)
    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb.worksheets[0]
    assert {ws.cell(r, 2).value: ws.cell(r, 3).value for r in range(2, 7)}["Торт"] == "Продукция производства"
    for r in range(2, 1001):
        for c in range(1, 7):
            ws.cell(r, c).value = None
    rows = [["K-1", None, "доготовка", None, "D-1", 0.1],
            ["K-1", None, None, None, "Стакан", 1],
            ["C-1", None, "продукция", "Пекарня"],
            ["NOPE", None, "покупной"],
            ["T-1", None, "что-то"]]
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            ws.cell(r, c).value = value
    out = io.BytesIO()
    wb.save(out)
    resp = client.post(f"{o}/import/routes", headers={**h, "Content-Type": "application/octet-stream"},
                       content=out.getvalue())
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["rows"], result["updated"]) == (5, 2)
    assert [e["row"] for e in result["errors"]] == [5, 6]
    assert [c["quantity"] for c in client.get(url, headers=h).json()] == ["0.1000", "1.0000"]
    stuff = {n["article"]: n for n in client.get(f"{o}/nomenclature", headers=h).json()}
    assert (stuff["C-1"]["route"], stuff["C-1"]["producer_cfo_id"]) == ("product", bakery["id"])
