"""Реестр накладных поставщиков и платежи внутри группы (docs/TZ.md, раздел 24, «Детали 14.5»). Данные вымышленные."""

import datetime as dt
import io

from openpyxl import Workbook

from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import act, create, env  # noqa: F401

TODAY = dt.date.today()


def days(n):
    return TODAY + dt.timedelta(days=n)


def workbook(rows):
    """Как лист владельца: сверху итоги, заголовок в 5-й строке, лишние колонки."""
    wb = Workbook()
    wb.active.title = "ТЕКУЩИЕ"
    ws = wb.create_sheet("ПОСТАВЩИКИ")
    ws.append([None, None, None, None, None, "оплата"])
    for _ in range(3):
        ws.append([None, "Поставщик 14 дней"])
    ws.append(["Тип документа", "Дата документа", "№ документа", "Сумма без НДС, р.", "Контрагент", "просрочка",
               "Дата оплаты", 1, 2])
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def upload(client, env, raw, h=None, **params):
    return client.post(f"{env['o']}/supplier-invoices/import", params={"legal_entity_id": env["le"].id, **params},
                       headers={**(h or env["h"]), "Content-Type": "application/octet-stream"}, content=raw)


def invoices(client, env, **params):
    resp = client.get(f"{env['o']}/supplier-invoices", headers=env["h"], params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_import_plan_and_limits(client, env, db, make_user, headers_for):
    from app.models import Membership

    fm = make_user(login="fm")
    db.add(Membership(user_id=fm.id, organization_id=env["org"].id, role="finance_manager"))
    db.commit()
    h_fm = headers_for(fm)
    resp = client.patch(f"/api/contracts/{env['contract']['id']}", headers=env["h"],
                        json={"deferral_days": 14, "debt_limit": "40000"})
    assert resp.status_code == 200 and resp.json()["deferral_days"] == 14, resp.text

    old = days(-30)
    raw = workbook([
        ["Приходная накладная", old.strftime("%d.%m.%Y"), "1001", 10000, "Арендодатель", "сб", None],
        ["Приходная накладная", dt.datetime.combine(days(-5), dt.time(9)), 1002, 25000.0, "ООО «Арендодатель»", None,
         dt.datetime(2026, 1, 1)],
        ["аванс ", TODAY, "17", "5 000,00", "арендодатель", None, None],
        ["Приходная накладная", old, "1001", 10000, "Арендодатель", None, None],  # повтор в файле
        ["Приходная накладная", old, "9", 100, "Неизвестный", None, None],
        ["Приходная накладная", old, "10", 100, "Неизвестный", None, None],
        ["Корректировка", old, "11", -100, "Арендодатель", None, None],
        [None, None, None, None, None, None, None],
    ])
    resp = upload(client, env, raw)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["rows"], result["created"], result["duplicates"]) == (7, 3, 1)
    assert result["missing"] == [["Неизвестный", 2]]
    assert [e[0] for e in result["errors"]] == [12]  # корректировка
    assert upload(client, env, raw).json()["created"] == 0  # повторная загрузка не дублирует
    assert upload(client, env, b"not excel").status_code == 422

    data = invoices(client, env)
    group = data["groups"][0]
    assert (group["counterparty_name"], group["deferral_days"], group["debt_limit"], group["debt"], group["over_limit"]) \
        == ("Арендодатель", 14, "40000.00", "40000.00", False)
    assert group["overdue"] == "10000.00"  # срок 1001 — дата + 14 дней — прошёл
    first, advance, second = group["invoices"]  # по сроку оплаты
    assert (first["number"], first["due_date"], first["overdue_days"]) == ("1001", days(-16).isoformat(), 16)
    assert (advance["kind"], advance["due_date"], second["number"], second["due_date"]) == \
        ("advance", TODAY.isoformat(), "1002", days(9).isoformat())

    # частичная оплата — от старых к новым; маршрут: финансовый менеджер → финансовый директор
    pay = f"{env['o']}/supplier-payments"
    body = {"legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"], "pay_date": days(1).isoformat()}
    assert client.post(pay, headers=env["h"], json={**body, "amount": "40000.01"}).status_code == 422
    resp = client.post(pay, headers=env["h"], json={**body, "amount": "12000", "bank_account_id": env["bank_acc"]["id"]})
    assert resp.status_code == 201, resp.text
    req = resp.json()
    assert (req["source"], req["route"], req["status"], req["amount"]) == ("supplier", ["fm", "fd"], "pending", "12000.00")
    assert "№ 1001" in req["description"] and "аванс № 17" in req["description"]
    first, advance, second = invoices(client, env)["groups"][0]["invoices"]
    assert (first["planned"], first["remaining"], advance["planned"], advance["remaining"]) == \
        ("10000.00", "0.00", "2000.00", "3000.00")
    assert first["payments"][0]["number"] == req["number"]
    # сумму оплаты поставщику не правят, отклонённую не подают снова — накладные освобождаются
    assert act(client, env, req, "reject", h=h_fm, reason="позже").json()["status"] == "rejected"
    assert invoices(client, env)["groups"][0]["remaining"] == "40000.00"
    assert act(client, env, req, "submit").status_code == 422

    # весь долг по одной накладной → согласование → оплата
    req = client.post(pay, headers=env["h"], json={**body, "invoice_ids": [second["id"]]}).json()
    assert req["amount"] == "25000.00"
    act(client, env, req, "approve", h=h_fm)
    assert act(client, env, req, "approve").json()["status"] == "approved"
    act(client, env, req, "pay", paid_on=TODAY.isoformat())
    group = invoices(client, env)["groups"][0]
    assert (group["debt"], len(group["invoices"])) == ("15000.00", 2)  # оплаченная накладная скрыта
    assert len(invoices(client, env, open_only=False)["groups"][0]["invoices"]) == 3

    # лимит задолженности
    client.patch(f"/api/contracts/{env['contract']['id']}", headers=env["h"], json={"debt_limit": "10000"})
    assert invoices(client, env)["groups"][0]["over_limit"] is True

    # удалить можно накладную без заявок
    assert client.delete(f"/api/supplier-invoices/{second['id']}", headers=env["h"]).status_code == 422
    assert client.delete(f"/api/supplier-invoices/{advance['id']}", headers=env["h"]).status_code == 204

    # права: CEO, руководитель ЦФО — нет; чужая организация — не видит
    for who, code in (("ceo", 403), ("head", 403), ("other", 404)):
        assert client.get(f"{env['o']}/supplier-invoices", headers=env[who]).status_code == code
        assert upload(client, env, raw, h=env[who]).status_code == code
    assert client.delete(f"/api/supplier-invoices/{first['id']}", headers=env["other"]).status_code == 404


def test_group_payment(client, env, db):
    from app.models import Bank, BankAccount, Currency, LegalEntity

    b = LegalEntity(organization_id=env["org"].id, full_name="ООО Б", short_name="Б", inn="7703333333")
    db.add(b)
    db.commit()
    rub = db.query(Currency).filter_by(code="RUB").one()
    bank = db.query(Bank).first()
    account = BankAccount(organization_id=env["org"].id, legal_entity_id=b.id, bank_id=bank.id, currency_id=rub.id,
                          number="40702810900000000077", name="Б основной")
    db.add(account)
    db.commit()
    post = env["post"]
    cp_b = post("counterparties", {"name": "Б (группа)", "kind": "legal", "inn": "7703333333",
                                   "account_number": "40702810900000000077"})
    post("counterparties", {"name": "А (группа)", "kind": "legal", "inn": "7701234567"})

    req = create(client, env, counterparty_id=cp_b["id"], contract_id=None, pay_date=days(3).isoformat(),
                 amount="70000", description="Займ").json()
    assert req["group_name"] == "Б"
    req = act(client, env, req, "submit").json()
    assert (req["status"], req["route"]) == ("approved", [])  # без согласования

    def mirror():
        rows = client.get(f"{env['o']}/payment-requests", headers=env["h"],
                          params={"direction": "in", "date_from": days(-40).isoformat(),
                                  "date_to": days(40).isoformat()}).json()["rows"]
        return rows[0] if rows else None

    m = mirror()
    assert (m["legal_entity_name"], m["counterparty_name"], m["amount"], m["status"], m["linked_number"]) == \
        ("Б", "А (группа)", "70000.00", "approved", req["number"])
    assert m["bank_account_id"] == account.id and m["pay_date"] == days(3).isoformat()
    # поступление меняется только вместе с выплатой
    assert act(client, env, m, "unapprove").status_code == 422
    assert act(client, env, m, "move", pay_date=days(5).isoformat()).status_code == 422
    act(client, env, req, "move", pay_date=days(5).isoformat(), bank_account_id=env["bank_acc"]["id"])
    assert mirror()["pay_date"] == days(5).isoformat()
    # выплату вернули — поступления нет
    assert act(client, env, req, "unapprove").json()["status"] == "pending"
    assert mirror() is None
