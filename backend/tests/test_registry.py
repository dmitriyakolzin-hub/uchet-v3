"""Реестр на оплату: по дням и счетам, проверка остатка, выгрузка платёжек в формате 1С, «отправлено в банк»
(docs/TZ.md, раздел 24, «Детали 14.4»). Данные вымышленные."""

import datetime as dt
import io

from openpyxl import load_workbook

from app.services.bank_statement import parse
from tests.test_bank_import import OUR, statement
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import act, create, env  # noqa: F401

TODAY = dt.date.today()
PAYEE_ACCOUNT = "40702810000000000088"


def approved(client, env, **fields):
    req = create(client, env, bank_account_id=env["bank_acc"]["id"], **fields).json()
    act(client, env, req, "submit")
    return act(client, env, req, "approve").json()


def registry(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/payment-registry", headers=h or env["h"], params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_registry_export_and_sent(client, env, db):
    from app.models import Bank

    late = approved(client, env, pay_date=(TODAY - dt.timedelta(days=3)).isoformat(), amount="30000",
                    description="Аренда, НДС не облагается")
    soon = approved(client, env, pay_date=(TODAY + dt.timedelta(days=2)).isoformat(), amount="25000",
                    description="Аренда")
    cash = create(client, env, pay_date=TODAY.isoformat(), cash_cfo_id=None, amount="100").json()  # черновик — нет
    assert cash["status"] == "draft"

    # на счёте 40 000 после поступления
    pay = env["art"]["Оплата поставщикам"]
    op = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": (TODAY - dt.timedelta(days=5)).isoformat(), "postings": [{
            "debit_account_id": env["acc"]["51"], "credit_account_id": env["acc"]["60.01"], "amount": "40000",
            "debit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": pay},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}]})
    assert op.status_code == 201, op.text

    data = registry(client, env)
    assert (data["count"], data["total"]) == (2, "55000.00")
    first, second = data["groups"]
    assert (first["pay_date"], first["overdue"], first["title"], first["total"], first["available"], first["shortage"]) \
        == (late["pay_date"], True, "Основной ··0001", "30000.00", "40000.00", "0.00")
    assert (second["available"], second["shortage"]) == ("10000.00", "15000.00")  # 40 000 − 30 000 < 25 000
    row = first["rows"][0]
    assert row["problems"] == ["у контрагента нет расчётного счёта", "у контрагента не указан банк"]
    assert second["rows"][0]["warnings"] and not row["warnings"]

    # без реквизитов получателя выгрузить нельзя
    url = f"{env['o']}/payment-registry/export"
    resp = client.post(url, headers=env["h"], json={"ids": [late["id"]]})
    assert resp.status_code == 422 and f"№ {late['number']}" in resp.json()["detail"]

    bank = Bank(bic="044525999", name="АО Банк Получателя", corr_account="30101810400000000999", city="г. Москва")
    db.add(bank)
    db.commit()
    resp = client.patch(f"/api/counterparties/{env['cp']['id']}", headers=env["h"],
                        json={"account_number": PAYEE_ACCOUNT, "bank_id": bank.id})
    assert resp.status_code == 200, resp.text
    assert client.patch(f"/api/counterparties/{env['cp']['id']}", headers=env["h"],
                        json={"account_number": "123"}).status_code == 422

    resp = client.post(url, headers=env["h"], json={"ids": [late["id"], soon["id"]]})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"].endswith('"1c_to_kl.txt"')
    text = resp.content.decode("cp1251")
    assert text.startswith("1CClientBankExchange\r\n") and text.count("СекцияДокумент=Платежное поручение") == 2
    assert f"Номер={late['number']}\r\n" in text and "Сумма=30000.00\r\n" in text
    assert f"ПолучательСчет={PAYEE_ACCOUNT}" in text and "ПолучательБИК=044525999" in text
    assert "ПолучательКорсчет=30101810400000000999" in text and "ПлательщикБИК=044525225" in text
    assert "Плательщик=ИНН 7701234567 ООО А" in text and "НазначениеПлатежа=Аренда, НДС не облагается" in text
    # наш же разбор выписок читает файл
    parsed = parse(resp.content)
    assert parsed.accounts == [OUR] and [d.amount for d in parsed.documents] == [30000, 25000]

    data = registry(client, env)
    assert all(r["sent_at"] for g in data["groups"] for r in g["rows"])
    assert registry(client, env, include_sent=False)["count"] == 0
    events = client.get(f"/api/payment-requests/{late['id']}/events", headers=env["h"]).json()
    assert events[-1]["action"] == "sent"

    # снять отметку; отметить без файла
    sent = f"{env['o']}/payment-registry/sent"
    assert client.post(sent, headers=env["h"], json={"ids": [soon["id"]], "sent": False}).status_code == 204
    assert [r["id"] for g in registry(client, env, include_sent=False)["groups"] for r in g["rows"]] == [soon["id"]]
    # вернули на согласование — отметка снимается, из реестра уходит
    assert act(client, env, late, "unapprove").json()["sent_at"] is None
    assert registry(client, env)["count"] == 1
    assert client.post(sent, headers=env["h"], json={"ids": [late["id"]]}).status_code == 422  # не согласована

    # Excel
    resp = client.get(f"{env['o']}/payment-registry.xlsx", headers=env["h"])
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    values = [c for row in ws.iter_rows(values_only=True) for c in row if c is not None]
    assert f"№ {soon['number']}" in values and "Реестр на оплату" in values

    # права: CEO и руководитель ЦФО реестр не видят, чужая организация — 404
    assert client.get(f"{env['o']}/payment-registry", headers=env["ceo"]).status_code == 403
    assert client.get(f"{env['o']}/payment-registry", headers=env["head"]).status_code == 403
    assert client.get(f"{env['o']}/payment-registry", headers=env["other"]).status_code == 404
    assert client.post(url, headers=env["ceo"], json={"ids": [soon["id"]]}).status_code == 403


def test_statement_remembers_requisites(client, env, db):
    from app.models import Bank, Counterparty

    bank = Bank(bic="044525999", name="АО Банк Получателя")
    db.add(bank)
    db.commit()
    us = (OUR, 'ООО "А"', "7701234567")
    raw = statement(OUR, [("71", "06.03.2026", "500", us, (PAYEE_ACCOUNT, "ООО Арендодатель", "7702222222"),
                           "Аренда")], date_from="01.03.2026", date_to="31.03.2026")
    raw = raw.replace("ПолучательРасчСчет=".encode("cp1251"), "ПолучательБИК=044525999\nПолучательРасчСчет=".encode("cp1251"))
    resp = client.post(f"{env['o']}/bank-statements", headers={**env["h"], "Content-Type": "application/octet-stream"},
                       content=raw)
    assert resp.status_code == 200, resp.text
    cp = db.get(Counterparty, env["cp"]["id"])
    db.refresh(cp)
    assert (cp.account_number, cp.bank_id) == (PAYEE_ACCOUNT, bank.id)
