"""Загрузка банковских выписок (формат 1CClientBankExchange) — на обезличенных данных."""

from decimal import Decimal

import pytest

from app.services.bank_statement import StatementError, parse

OUR = "40702810900000000001"
OUR2 = "40702810900000000002"
OUR_INN = "7701234567"
CUSTOMER = ("40802810000000000077", "ИП Покупатель", "770111111111")
SUPPLIER = ("40702810000000000088", "ООО Поставщик", "7702222222")
BANK = ("47423810000000000099", "ПАО Банк", "7707083893")
TREASURY = ("03100643000000018500", "Казначейство России (ФНС России)", "7727406020")


def doc(number, date, amount, payer, payee, purpose, kind="Платежное поручение", payment_kind="01", kbk=""):
    """payer / payee — (счёт, наименование, ИНН)."""
    debited = date if payer[0] in (OUR, OUR2) else ""
    credited = date if payee[0] in (OUR, OUR2) else ""
    return "\n".join([
        f"СекцияДокумент={kind}", f"Номер={number}", f"Дата={date}", f"Сумма={amount}",
        f"ПлательщикСчет={payer[0]}", f"ДатаСписано={debited}", f"Плательщик={payer[1]}", f"ПлательщикИНН={payer[2]}",
        f"ПлательщикРасчСчет={payer[0]}", f"ПолучательСчет={payee[0]}", f"ДатаПоступило={credited}",
        f"Получатель={payee[1]}", f"ПолучательИНН={payee[2]}", f"ПолучательРасчСчет={payee[0]}",
        f"ВидОплаты={payment_kind}", f"ПоказательКБК={kbk}", f"НазначениеПлатежа={purpose}", "КонецДокумента",
    ])


def statement(account, docs, opening="1000.00", date_from="01.10.2026", date_to="31.10.2026", encoding="cp1251"):
    total_in = sum((Decimal(d[2]) for d in docs if d[4][0] == account), Decimal(0))
    total_out = sum((Decimal(d[2]) for d in docs if d[3][0] == account), Decimal(0))
    closing = Decimal(opening) + total_in - total_out
    text = "\n".join([
        "1CClientBankExchange", "ВерсияФормата=1.03", "Кодировка=Windows", "Отправитель=Банк-клиент",
        f"ДатаНачала={date_from}", f"ДатаКонца={date_to}", f"РасчСчет={account}",
        "СекцияРасчСчет", f"ДатаНачала={date_from}", f"ДатаКонца={date_to}", f"НачальныйОстаток={opening}",
        f"РасчСчет={account}", f"ВсегоСписано={total_out}", f"ВсегоПоступило={total_in}",
        f"КонечныйОстаток={closing}", "КонецРасчСчет",
        *[doc(*d) for d in docs], "КонецФайла",
    ])
    return text.encode(encoding)


def us(account=OUR):
    return (account, 'ООО "Наша"', OUR_INN)


# --- Разбор файла ---


def test_parse_cp1251_and_utf8():
    docs = [("1", "02.10.2026", "500", CUSTOMER, us(), "Оплата по договору")]
    for encoding in ("cp1251", "utf-8"):
        st = parse(statement(OUR, docs, encoding=encoding))
        assert st.accounts == [OUR] and len(st.documents) == 1
        assert st.documents[0].payer_name == "ИП Покупатель"
        assert st.balances(OUR) == (Decimal("1000.00"), Decimal("1500.00"), Decimal("500"), Decimal("0"))


def test_parse_rejects_other_files():
    with pytest.raises(StatementError):
        parse("Просто текст".encode("cp1251"))
    with pytest.raises(StatementError):
        parse("1CClientBankExchange\nДатаНачала=99.99.2026\nРасчСчет=1".encode("cp1251"))


# --- Загрузка ---


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import Bank, Currency, LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО Наша", short_name="Наша", inn=OUR_INN)
    bank = Bank(bic="044525225", name="Банк")
    db.add_all([le, bank])
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    rub = db.query(Currency).filter_by(code="RUB").one()
    main = client.post(f"{o}/bank-accounts", headers=h, json={
        "legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id, "number": OUR, "name": "Основной"}).json()
    extra = client.post(f"{o}/bank-accounts", headers=h, json={
        "legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id, "number": OUR2, "name": "Второй"}).json()
    accounts = {a["id"]: a["code"] for a in client.get("/api/accounts", headers=h).json()}
    articles = {a["id"]: a["name"] for a in client.get("/api/articles", headers=h).json()}
    return {"org": org, "other": other, "le": le, "rub": rub, "main": main, "extra": extra, "h": h, "o": o,
            "acc": accounts, "art": articles, "headers_for": headers_for, "ceo": ceo, "other_fd": other_fd}


def upload(client, env, raw, h=None):
    return client.post(f"{env['o']}/bank-statements", headers={**(h or env["h"]), "Content-Type": "application/octet-stream"},
                       content=raw)


def operations(client, env):
    items = client.get(f"{env['o']}/operations", headers=env["h"], params={"limit": 1000}).json()["items"]
    return sorted(items, key=lambda o: o["number"])


MONTH = [
    ("11", "02.10.2026", "80000", CUSTOMER, us(), "Оплата по договору оказания услуг"),
    ("12", "06.10.2026", "5000", us(), TREASURY, "Единый налоговый платеж", "Платежное поручение", "01", "18201061201010000510"),
    ("13", "07.10.2026", "2400", us(), SUPPLIER, "Оплата по счёту 15"),
    ("14", "12.10.2026", "217.28", us(), BANK, "Комиссия за перечисления", "Банковский ордер", "17"),
    ("15", "13.10.2026", "27426", us(), BANK, "Заработная плата по реестру №26"),
    ("16", "14.10.2026", "5000", BANK, us(), "Выдача кредита по договору №1"),
]


def test_import_month(client, env):
    resp = upload(client, env, statement(OUR, MONTH))
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["created"] == [1, 2, 3, 4, 5, 6]
    assert set(result["new_counterparties"]) == {"ИП Покупатель", "ООО Поставщик", "ПАО Банк"}

    ops = operations(client, env)
    assert all(o["status"] == "draft" for o in ops)
    by_text = {o["description"].split(" (")[0]: o for o in ops}
    acc, art = env["acc"], env["art"]

    income = by_text["Оплата по договору оказания услуг"]
    p = income["postings"][0]
    assert art[income["article_id"]] == "Поступление от покупателей"
    assert (acc[p["debit_account_id"]], acc[p["credit_account_id"]], p["amount"]) == ("51", "62.01", "80000.00")
    assert p["debit"]["bank_account"] == env["main"]["id"]
    assert income["op_date"] == "2026-10-02"
    assert "№ 11 от 02.10.2026, ИП Покупатель" in income["description"]

    assert art[by_text["Единый налоговый платеж"]["article_id"]] == "Уплата прочих налогов"
    assert art[by_text["Оплата по счёту 15"]["article_id"]] == "Оплата поставщикам"
    assert art[by_text["Комиссия за перечисления"]["article_id"]] == "Банковские комиссии"
    assert art[by_text["Заработная плата по реестру №26"]["article_id"]] == "Выплата заработной платы"
    loan = by_text["Выдача кредита по договору №1"]
    assert art[loan["article_id"]] == "Получение кредитов и займов"
    assert acc[loan["postings"][0]["credit_account_id"]] == "66"

    # налог проводится сразу; где не хватает договора / ЦФО / сотрудника — неполные черновики
    tax_number = by_text["Единый налоговый платеж"]["number"]
    assert tax_number not in result["incomplete"]
    for text in ("Комиссия за перечисления", "Заработная плата по реестру №26", "Оплата по договору оказания услуг"):
        assert by_text[text]["number"] in result["incomplete"], text

    check = result["accounts"][0]
    assert check["totals_match"] is True
    assert (check["statement_opening"], check["statement_closing"]) == ("1000.00", "50956.72")
    assert check["system_closing"] == "0.00"  # ничего ещё не проведено


def test_reimport_creates_no_duplicates(client, env):
    upload(client, env, statement(OUR, MONTH))
    again = upload(client, env, statement(OUR, MONTH)).json()
    assert again["created"] == [] and again["skipped_duplicates"] == len(MONTH)
    assert len(operations(client, env)) == len(MONTH)


def test_contract_and_history_are_reused(client, env):
    o, h = env["o"], env["h"]
    upload(client, env, statement(OUR, [MONTH[0]]))
    cp = next(c for c in client.get(f"{o}/counterparties", headers=h).json() if c["inn"] == CUSTOMER[2])
    contract = client.post(f"{o}/contracts", headers=h, json={
        "counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": env["rub"].id,
        "kind": "customer", "number": "Д-1"}).json()
    # единственный договор подставляется сам, черновик сразу полный
    second = upload(client, env, statement(OUR, [("21", "20.10.2026", "1000", CUSTOMER, us(), "Ещё оплата")])).json()
    op = operations(client, env)[-1]
    assert op["postings"][0]["credit"]["contract"] == contract["id"]
    assert second["incomplete"] == []

    # статью поменяли вручную — следующий платёж этого контрагента получит её же
    art_ids = {v: k for k, v in env["art"].items()}
    body = {"legal_entity_id": op["legal_entity_id"], "op_date": op["op_date"], "article_id": art_ids["Прочие поступления"],
            "description": op["description"], "postings": [{
                "debit_account_id": op["postings"][0]["debit_account_id"], "credit_account_id": op["postings"][0]["credit_account_id"],
                "amount": op["postings"][0]["amount"], "debit": op["postings"][0]["debit"], "credit": op["postings"][0]["credit"]}]}
    assert client.put(f"/api/operations/{op['id']}", headers=h, json=body).status_code == 200
    upload(client, env, statement(OUR, [("22", "25.10.2026", "700", CUSTOMER, us(), "Третья оплата")]))
    assert env["art"][operations(client, env)[-1]["article_id"]] == "Прочие поступления"


def test_transfer_between_own_accounts_not_doubled(client, env):
    transfer = ("31", "15.10.2026", "3000", us(OUR), us(OUR2), "Перевод собственных средств")
    first = upload(client, env, statement(OUR, [transfer])).json()
    second = upload(client, env, statement(OUR2, [transfer], opening="0.00")).json()
    assert len(first["created"]) == 1 and second["skipped_duplicates"] == 1
    op = operations(client, env)[0]
    p = op["postings"][0]
    assert env["art"][op["article_id"]] == "Перевод между своими счетами"
    assert (p["debit"]["bank_account"], p["credit"]["bank_account"]) == (env["extra"]["id"], env["main"]["id"])
    assert first["incomplete"] == []
    # после проведения деньги перешли со счёта на счёт
    assert client.post(f"/api/operations/{op['id']}/post", headers=env["h"]).status_code == 200
    rows = {r["name"]: r for r in client.get(f"{env['o']}/money", headers=env["h"],
                                             params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).json()["rows"]}
    assert rows["Основной"]["closing"] == "-3000.00" and rows["Второй"]["closing"] == "3000.00"


def test_reconciliation_after_posting(client, env):
    """После проведения всех платежей остаток в системе совпадает с выпиской."""
    client.patch(env["o"], headers=env["h"], json={"opening_balance_date": "2026-09-30"})
    client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-09-30", "is_opening": True, "postings": [{
            "debit_account_id": next(k for k, v in env["acc"].items() if v == "51"),
            "credit_account_id": next(k for k, v in env["acc"].items() if v == "00"), "amount": "1000",
            "debit": {"bank_account": env["main"]["id"],
                      "cash_flow_item": next(k for k, v in env["art"].items() if v == "Прочие поступления")}}]})
    docs = [MONTH[1], ("41", "20.10.2026", "200", us(), TREASURY, "НДФЛ", "Платежное поручение", "01", "18210102010011000110")]
    result = upload(client, env, statement(OUR, docs)).json()
    assert result["incomplete"] == []
    ids = [o["id"] for o in operations(client, env) if not o["is_opening"]]
    assert client.post(f"{env['o']}/operations/bulk", headers=env["h"], json={"action": "post", "ids": ids}).json()["done"] == 2
    check = upload(client, env, statement(OUR, docs)).json()["accounts"][0]
    assert check["system_opening"] == check["statement_opening"] == "1000.00"
    assert check["system_closing"] == check["statement_closing"] == "-4200.00"


def test_documents_before_opening_date_skipped(client, env):
    client.patch(env["o"], headers=env["h"], json={"opening_balance_date": "2026-10-05"})
    result = upload(client, env, statement(OUR, MONTH)).json()
    assert result["skipped_before_opening"] == 1  # платёж от 02.10 уже в начальных остатках
    assert len(result["created"]) == len(MONTH) - 1


def test_unknown_account(client, env):
    resp = upload(client, env, statement("40702810999999999999", [MONTH[0]]))
    assert resp.status_code == 422 and "40702810999999999999 не заведён" in resp.json()["detail"]
    assert operations(client, env) == []


def test_bad_file(client, env):
    assert upload(client, env, b"").status_code == 422
    assert upload(client, env, "не выписка".encode("cp1251")).status_code == 422


def test_rights(client, env):
    raw = statement(OUR, MONTH)
    assert upload(client, env, raw, h=env["headers_for"](env["ceo"])).status_code == 403
    assert upload(client, env, raw, h=env["headers_for"](env["other_fd"])).status_code == 404
