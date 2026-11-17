"""Движение имущества, шаг 19.1: роли, единицы, видимость, история, групповое изменение, Excel, связь с ОС
(docs/TZ.md, раздел 28, «Детали 19.1»). Данные вымышленные."""

import io

from openpyxl import Workbook, load_workbook

from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401


def member(db, make_user, headers_for, env, login, role):
    from app.models import Membership

    user = make_user(login=login, full_name=login.capitalize())
    db.add(Membership(user_id=user.id, organization_id=env["org"].id, role=role))
    db.commit()
    return user, headers_for(user)


def items(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/asset-items", headers=h or env["h"], params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_items_roles_and_history(client, env, db, make_user, headers_for):  # noqa: F811
    aho_user, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    _, accountant = member(db, make_user, headers_for, env, "buh", "accountant")
    cats = {c["name"]: c["id"] for c in client.get(f"{env['o']}/asset-categories", headers=aho).json()}
    assert "Инструмент" in cats  # стартовые категории
    assert client.post(f"{env['o']}/asset-categories", headers=aho, json={"name": "Кухня"}).status_code == 201
    shop, office = env["shop"]["id"], env["office"]["id"]

    # МОЛ — сотрудник, связанный с пользователем
    person = env["post"]("employees", {"full_name": "Матвеев Олег", "user_id": mol_user.id})
    assert person["user_id"] == mol_user.id
    other_user = make_user(login="stranger")
    assert client.post(f"{env['o']}/employees", headers=env["h"], json={
        "full_name": "Чужой", "user_id": other_user.id}).status_code == 422  # не работает в организации
    assert client.post(f"{env['o']}/employees", headers=env["h"], json={
        "full_name": "Двойник", "user_id": mol_user.id}).status_code == 422  # уже связан
    members = client.get(f"{env['o']}/members", headers=env["h"]).json()
    assert {"user_id": mol_user.id, "name": "Mol", "role_title": "МОЛ"} in members

    body = {"name": "Стул офисный", "category_id": cats["Мебель"], "cfo_id": office, "employee_id": person["id"],
            "purchase_price": "3500", "count": 3}
    assert client.post(f"{env['o']}/asset-items", headers=engineer, json=body).status_code == 403
    resp = client.post(f"{env['o']}/asset-items", headers=aho, json=body)
    assert resp.status_code == 201, resp.text
    chairs = resp.json()
    assert [c["number"] for c in chairs] == ["ИН-000001", "ИН-000002", "ИН-000003"]
    assert (chairs[0]["status_title"], chairs[0]["category_name"], chairs[0]["employee_name"]) == \
        ("На складе", "Мебель", "Матвеев Олег")
    drill = client.post(f"{env['o']}/asset-items", headers=accountant, json={
        "name": "Дрель", "number": "ИН-000002", "cfo_id": shop}).status_code
    assert drill == 422  # номер занят
    resp = client.post(f"{env['o']}/asset-items", headers=accountant, json={
        "name": "Дрель", "cfo_id": shop, "ownership": "leasing", "serial_number": "SN-77"})
    assert resp.status_code == 201 and resp.json()[0]["number"] == "ИН-000004"
    drill = resp.json()[0]
    assert client.post(f"{env['o']}/asset-items", headers=aho, json={
        "name": "Х", "number": "N-1", "count": 2}).status_code == 422

    # видимость: МОЛ — только своё, руководитель ЦФО — своя ветка (офис), инженер — всё
    assert len(items(client, env, mol)) == 3 and all(i["mine"] for i in items(client, env, mol))
    assert [i["name"] for i in items(client, env, env["head"])] == ["Стул офисный"] * 3
    assert len(items(client, env, engineer)) == 4
    assert client.get(f"/api/asset-items/{drill['id']}", headers=mol).status_code == 404
    assert client.get(f"{env['o']}/asset-items", headers=env["other"]).status_code == 404
    assert [i["number"] for i in items(client, env, search="sn-77")] == ["ИН-000004"]
    assert len(items(client, env, ownership="leasing")) == 1
    assert len(items(client, env, mine=True)) == 0  # у финансового директора своих нет

    # правка — история «было → стало»; МОЛ и инженер не правят
    edit = {**body, "name": "Стул офисный", "status": "in_use", "cfo_id": shop}
    edit.pop("count")
    assert client.put(f"/api/asset-items/{chairs[0]['id']}", headers=mol, json=edit).status_code == 403
    resp = client.put(f"/api/asset-items/{chairs[0]['id']}", headers=aho, json=edit)
    assert resp.status_code == 200 and resp.json()["number"] == "ИН-000001"
    events = client.get(f"/api/asset-items/{chairs[0]['id']}/events", headers=mol).json()
    assert [e["action"] for e in events] == ["created", "edited"]
    assert "место: Офис → Магазин" in events[1]["details"] and "статус: На складе → В эксплуатации" in events[1]["details"]

    # групповое изменение: МОЛ убран, склад не трогаем
    ids = [c["id"] for c in chairs[1:]]
    resp = client.post(f"{env['o']}/asset-items/bulk", headers=aho, json={"ids": ids, "employee_id": None})
    assert resp.status_code == 200 and all(i["employee_id"] is None for i in resp.json())
    assert "МОЛ: Матвеев Олег → —" in client.get(f"/api/asset-items/{ids[0]}/events", headers=aho).json()[-1]["details"]
    assert client.post(f"{env['o']}/asset-items/bulk", headers=aho, json={"ids": ids}).status_code == 422
    assert client.post(f"{env['o']}/asset-items/bulk", headers=env["other"],
                       json={"ids": ids, "status": "repair"}).status_code == 404

    # файлы
    url = f"/api/asset-items/{drill['id']}/files"
    resp = client.post(url, headers={**aho, "Content-Type": "application/octet-stream"},
                       params={"filename": "паспорт.pdf"}, content=b"%PDF-1.4")
    assert resp.status_code == 201, resp.text
    file_id = resp.json()["id"]
    assert client.get(f"/api/asset-item-files/{file_id}", headers=engineer).content == b"%PDF-1.4"
    assert client.get(f"/api/asset-items/{drill['id']}", headers=aho).json()["files"][0]["filename"] == "паспорт.pdf"
    assert client.delete(f"/api/asset-item-files/{file_id}", headers=engineer).status_code == 403
    assert client.delete(f"/api/asset-item-files/{file_id}", headers=aho).status_code == 204

    # удалить заведённую по ошибке
    assert client.delete(f"/api/asset-items/{drill['id']}", headers=aho).status_code == 204
    assert len(items(client, env)) == 3


def test_fixed_assets_and_excel(client, env, db, make_user, headers_for):  # noqa: F811
    from app.models import FixedAsset

    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    db.add(FixedAsset(organization_id=env["org"].id, name="Печь", inventory_number="ОС-7", legal_entity_id=env["le"].id,
                      cost=500000, useful_life_months=60))
    db.commit()
    rows = items(client, env)
    assert [(i["number"], i["name"], i["status"]) for i in rows] == [("ОС-7", "Печь", "stock")]
    assert rows[0]["fixed_asset_id"] and rows[0]["purchase_price"] == "500000.00"
    assert client.delete(f"/api/asset-items/{rows[0]['id']}", headers=aho).status_code == 422
    assert len(items(client, env)) == 1  # повторно не создаётся

    person = env["post"]("employees", {"full_name": "Петров Пётр", "personnel_number": "17"})
    wb = Workbook()
    ws = wb.active
    ws.append(["Наименование", "Количество", "Инвентарный №", "Категория", "Владение", "Юрлицо (ИНН или название)",
               "Серийный №", "Место (ЦФО)", "Склад", "МОЛ", "Статус", "Цена приобретения", "Дата приобретения"])
    ws.append(["Нож поварской", 4, None, "Инструмент", None, "7701234567", None, "Магазин", None, "17",
               "в эксплуатации", "1200,50", "01.10.2026"])
    ws.append(["Касса", None, "К-1", "Оборудование", "аренда", None, "SN1", "Офис", None, "Петров Пётр", None, 15000, None])
    ws.append(["Касса 2", None, "К-1", None, None, None, None, None, None, None, None, None, None])  # номер занят
    ws.append(["Миксер", None, None, "Посуда", None, None, None, None, None, None, None, None, None])
    ws.append(["Весы", 2, "В-1", None, None, None, None, None, None, None, None, None, None])
    out = io.BytesIO()
    wb.save(out)
    resp = client.post(f"{env['o']}/import/asset-items", headers={**aho, "Content-Type": "application/octet-stream"},
                       content=out.getvalue())
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["rows"], result["created"]) == (5, 5), result
    assert [e[0] for e in result["errors"]] == [4, 5, 6]
    knives = items(client, env, search="Нож")
    assert len(knives) == 4 and knives[0]["status"] == "in_use" and knives[0]["employee_id"] == person["id"]
    assert knives[0]["purchase_price"] == "1200.50" and knives[0]["cfo_name"].endswith("Магазин")
    till = items(client, env, search="К-1")[0]
    assert (till["ownership"], till["category_name"]) == ("rent", "Оборудование")
    events = client.get(f"/api/asset-items/{till['id']}/events", headers=aho).json()
    assert events[0]["action"] == "imported"

    resp = client.get(f"{env['o']}/asset-items.xlsx", headers=env["head"])
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    numbers = [v for r in range(2, ws.max_row + 1) if (v := ws.cell(row=r, column=3).value)]
    assert numbers == ["К-1"]  # руководитель ЦФО видит свою ветку (офис); печь без места и ножи магазина — нет
    assert client.get(f"{env['o']}/import/templates/asset-items.xlsx", headers=env["ceo"]).status_code == 403
