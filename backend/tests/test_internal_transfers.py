"""Внутренние передачи по количеству, шаг 20.4: суммы по внутренним ценам, отмена, загрузка из Excel, передачи
по перемещениям из журнала (docs/TZ.md, раздел 29, «Детали 20.4»)."""

import datetime as dt
import io
from decimal import Decimal

from openpyxl import load_workbook

from tests.test_closing import env  # noqa: F401

DAY = dt.date.today().replace(day=1).isoformat()


def test_internal_transfers(client, env, db):  # noqa: F811
    from app.models import Operation

    post, h, o, acc = env["post"], env["h"], env["o"], env["acc"]
    unit = client.get("/api/units", headers=h).json()[0]["id"]
    milk = post("nomenclature", {"name": "Молоко 1 л", "article": "M-1", "unit_id": unit, "kind": "product"})
    cheese = post("nomenclature", {"name": "Сыр", "article": "S-1", "unit_id": unit, "kind": "product"})
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    prod = post("cfos", {"name": "Производство", "parent_id": company["id"], "chain_link": "production"})
    logistics = post("cfos", {"name": "Логистика", "parent_id": company["id"], "chain_link": "logistics"})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"], "chain_link": "trade"})
    shop = post("cfos", {"name": "Магазин 1", "parent_id": trade["id"]})
    nolink = post("cfos", {"name": "Без звена", "parent_id": company["id"]})
    prices = f"{o}/nomenclature-prices"
    client.post(prices, headers=h, json={"nomenclature_id": milk["id"], "valid_from": "2020-01-01",
                                         "production": "50", "logistics": "60"})
    # у магазина своя цена логистики, с НДС 20 %: без НДС — 65
    client.post(prices, headers=h, json={"nomenclature_id": milk["id"], "cfo_id": shop["id"], "valid_from": "2020-01-01",
                                         "logistics": "78", "vat_included": True, "vat_rate": 20})

    url = f"{o}/internal-transfers"
    body = {"on_date": DAY, "from_cfo_id": prod["id"], "to_cfo_id": logistics["id"],
            "lines": [{"nomenclature_id": milk["id"], "quantity": "10"}]}
    preview = client.post(f"{url}/preview", headers=env["ceo"], json=body).json()
    assert Decimal(preview["total"]) == 500 and preview["lines"][0]["price_title"] == "Цена производства"
    assert client.post(url, headers=env["ceo"], json=body).status_code == 403
    assert client.post(url, headers=env["head"], json=body).status_code == 403
    resp = client.post(url, headers=h, json={**body, "lines": [*body["lines"], {"nomenclature_id": cheese["id"],
                                                                                "quantity": "1"}]})
    assert resp.status_code == 422 and "Сыр" in resp.json()["detail"]  # нет цены сыра
    resp = client.post(url, headers=h, json={**body, "lines": [*body["lines"],
                                                               {"title": "Тара", "amount": "15.50"}]})
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert (doc["transfer_number"], Decimal(doc["total"]), doc["source"]) == (1, Decimal("515.50"), "manual")
    assert [line["price_title"] for line in doc["lines"]] == ["Цена производства", "вручную"]
    assert db.get(Operation, doc["operation_id"]).status == "posted"
    # звено не указано — по количеству нельзя, сумма вручную — можно
    nolink_body = {**body, "from_cfo_id": nolink["id"]}
    assert "звено" in client.post(url, headers=h, json=nolink_body).json()["detail"]
    assert client.post(url, headers=h, json={**nolink_body, "lines": [{"amount": "10"}]}).status_code == 201
    # как в 20.1: одна сумма без строк
    assert client.post(url, headers=h, json={"on_date": DAY, "from_cfo_id": prod["id"], "to_cfo_id": logistics["id"],
                                             "amount": "7"}).status_code == 201

    # отмена — операция удаляется
    cancelled = client.post(f"/api/internal-transfers/{doc['id']}/cancel", headers=h).json()
    assert cancelled["status"] == "cancelled" and cancelled["operation_id"] is None
    db.expire_all()
    assert db.get(Operation, doc["operation_id"]) is None
    assert client.post(f"/api/internal-transfers/{doc['id']}/cancel", headers=h).status_code == 422
    assert len(client.get(url, headers=env["ceo"]).json()) == 2
    assert len(client.get(url, headers=env["ceo"], params={"with_cancelled": True}).json()) == 3

    # из журнала: перемещение со склада логистики на склад магазина → передача по цене логистики магазина
    wh_log = post("warehouses", {"name": "Склад логистики", "cfo_id": logistics["id"]})
    wh_shop = post("warehouses", {"name": "Склад магазина", "cfo_id": shop["id"]})
    move = client.post(f"{o}/operations?post=true", headers=h, json={
        "legal_entity_id": env["le"].id, "op_date": DAY, "description": "Перемещение", "postings": [{
            "debit_account_id": acc["41"], "credit_account_id": acc["41"], "amount": "200", "quantity": "4",
            "debit": {"nomenclature": milk["id"], "warehouse": wh_shop["id"]},
            "credit": {"nomenclature": milk["id"], "warehouse": wh_log["id"]}}]})
    assert move.status_code == 201, move.text
    period = {"date_from": DAY, "date_to": dt.date.today().isoformat()}
    props = client.get(f"{url}/journal", headers=env["ceo"], params=period).json()
    assert len(props) == 1 and props[0]["from_name"] == "Логистика" and props[0]["to_name"] == "Магазин 1"
    assert Decimal(props[0]["total"]) == 260 and props[0]["error"] == ""
    made = client.post(f"{url}/from-journal", headers=h, json={**period, "operation_ids": [move.json()["id"]]}).json()
    assert len(made["created"]) == 1 and made["errors"] == []
    assert client.get(f"{url}/journal", headers=env["ceo"], params=period).json() == []  # повторно не предлагается
    journal_doc = next(d for d in client.get(url, headers=h).json() if d["source"] == "journal")
    assert journal_doc["source_operation_number"] == move.json()["number"]

    # Excel: две строки одного документа, документ с ошибкой не загружается целиком
    template = client.get(f"{o}/import/templates/internal-transfers.xlsx", headers=h)
    wb = load_workbook(io.BytesIO(template.content))
    ws = wb.worksheets[0]
    rows = [[dt.date.fromisoformat(DAY), "Производство", "Логистика", "M-1", None, 2],
            [dt.date.fromisoformat(DAY), "Производство", "Логистика", None, "Тара", None, 3],
            [dt.date.fromisoformat(DAY), "Производство", "Магазин 1", "S-1", None, 1],  # нет цены сыра
            [dt.date.fromisoformat(DAY), "Нет такого", "Логистика", "M-1", None, 1]]
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            ws.cell(r, c).value = value
    out = io.BytesIO()
    wb.save(out)
    resp = client.post(f"{o}/import/internal-transfers", headers={**h, "Content-Type": "application/octet-stream"},
                       content=out.getvalue())
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["rows"] == 4 and len(result["created"]) == 1
    assert [e["row"] for e in result["errors"]] == [4, 5]
    excel_doc = next(d for d in client.get(url, headers=h).json() if d["source"] == "excel")
    assert Decimal(excel_doc["total"]) == 103  # 2 × 50 + 3 вручную
