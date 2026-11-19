"""Внутренние цены по SKU и звенья цепочки, шаг 20.3 (docs/TZ.md, раздел 29, «Детали 20.3»)."""

import datetime as dt
import io
from decimal import Decimal

from openpyxl import load_workbook

from tests.test_closing import env  # noqa: F401


def test_prices(client, env, db):  # noqa: F811
    from app.services import prices

    post, h, o = env["post"], env["h"], env["o"]
    unit = client.get("/api/units", headers=h).json()[0]["id"]
    milk = post("nomenclature", {"name": "Молоко 1 л", "article": "M-1", "unit_id": unit, "kind": "product"})
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"], "chain_link": "trade"})
    south = post("cfos", {"name": "Филиал Юг", "parent_id": trade["id"]})
    shop = post("cfos", {"name": "Магазин 1", "parent_id": south["id"]})
    assert trade["chain_link"] == "trade" and south["chain_link"] is None
    assert client.post(f"{o}/cfos", headers=h, json={"name": "X", "chain_link": "bad"}).status_code == 422
    from app.models import Cfo

    assert prices.chain_link(db, db.get(Cfo, shop["id"])) == "trade"  # звено — от родителя
    assert prices.chain_link(db, db.get(Cfo, company["id"])) is None

    url = f"{o}/nomenclature-prices"
    body = {"nomenclature_id": milk["id"], "valid_from": "2026-01-01", "production": "50", "logistics": "60",
            "sale": "100", "discount": "70"}
    assert client.post(url, headers=env["ceo"], json=body).status_code == 403  # только просмотр
    assert client.post(url, headers=h, json={**body, "production": None, "logistics": None, "sale": None,
                                             "discount": None}).status_code == 422  # нужна хотя бы одна цена
    assert client.post(url, headers=h, json={**body, "vat_rate": 13}).status_code == 422
    common = client.post(url, headers=h, json=body)
    assert common.status_code == 201, common.text
    assert client.post(url, headers=h, json=body).status_code == 422  # та же дата и тот же ЦФО
    # с 1 марта — новая общая цена; у филиала Юг своя цена продажи с НДС 20 %
    client.post(url, headers=h, json={**body, "valid_from": "2026-03-01", "production": "55"})
    south_price = client.post(url, headers=h, json={"nomenclature_id": milk["id"], "cfo_id": south["id"],
                                                    "valid_from": "2026-02-01", "sale": "120", "vat_included": True,
                                                    "vat_rate": 20}).json()
    assert south_price["cfo_name"] == "Филиал Юг"

    def look(kind, on, cfo=None):
        params = {"nomenclature_id": milk["id"], "kind": kind, "on_date": on, **({"cfo_id": cfo} if cfo else {})}
        resp = client.get(f"{url}/lookup", headers=env["ceo"], params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    assert Decimal(look("production", "2026-02-15")["price"]) == 50
    assert Decimal(look("production", "2026-03-01")["price"]) == 55
    assert look("production", "2025-12-31")["price"] is None  # раньше первой цены
    sale = look("sale", "2026-02-15", shop["id"])  # магазин — цена своего филиала, без НДС — по ставке
    assert (Decimal(sale["price"]), Decimal(sale["net"]), sale["cfo_id"]) == (120, 100, south["id"])
    assert Decimal(look("sale", "2026-01-15", shop["id"])["price"]) == 100  # филиал ещё без цены — общая
    assert Decimal(look("logistics", "2026-02-15", shop["id"])["price"]) == 60  # логистики у филиала нет — общая

    # список, правка, удаление
    rows = client.get(url, headers=env["ceo"], params={"nomenclature_id": milk["id"]}).json()
    assert len(rows) == 3
    resp = client.put(f"/api/nomenclature-prices/{south_price['id']}", headers=h,
                      json={**{k: south_price[k] for k in ("nomenclature_id", "cfo_id", "valid_from")}, "sale": "130",
                            "vat_included": False})
    assert resp.status_code == 200 and Decimal(resp.json()["sale"]) == 130
    assert client.delete(f"/api/nomenclature-prices/{south_price['id']}", headers=env["ceo"]).status_code == 403
    assert client.delete(f"/api/nomenclature-prices/{south_price['id']}", headers=h).status_code == 204
    assert client.get(url, headers=env["other"]).status_code == 404  # чужая организация

    # Excel: шаблон со всей номенклатурой и загрузка (обновление той же даты, новая дата, ошибки по строкам)
    template = client.get(f"{o}/import/prices.xlsx", headers=h)
    assert template.status_code == 200
    ws = load_workbook(io.BytesIO(template.content)).worksheets[0]
    assert ws.cell(2, 1).value == "M-1" and Decimal(str(ws.cell(2, 5).value)) == 55
    ws.cell(2, 4).value = dt.date(2026, 3, 1)
    ws.cell(2, 7).value = 110  # цена продажи на ту же дату — обновление
    # строки — по номеру: шаблон размечает текстовые колонки на 1000 строк, append ушёл бы ниже
    for r, values in enumerate([["M-1", None, "Филиал Юг", dt.date(2026, 4, 1), None, None, 125, None, "да", 20],
                                ["NOPE", None, None, dt.date(2026, 4, 1), 1],
                                [None, "Молоко 1 л", None, None, 1]], start=3):
        for c, value in enumerate(values, start=1):
            ws.cell(r, c).value = value
    out = io.BytesIO()
    ws.parent.save(out)
    resp = client.post(f"{o}/import/prices", headers={**h, "Content-Type": "application/octet-stream"},
                       content=out.getvalue())
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["rows"], result["created"], result["updated"]) == (4, 1, 1)
    assert [e["row"] for e in result["errors"]] == [4, 5]
    assert Decimal(look("sale", "2026-03-15")["price"]) == 110
    assert Decimal(look("sale", "2026-04-02", shop["id"])["net"]) == Decimal("104.17")
