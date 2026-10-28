"""Управленческий отчёт по форме организации: статьи, показатели по формулам, план/факт
(docs/TZ.md, раздел 19, «Детали 9.4»). Форма — по образцу «Малины», названия вымышленные."""

from app.services.management_report import FormulaError, evaluate, parse_formula
from tests.test_article_import import load, preview, xlsx
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

FORM = [
    ("ОПЕРАЦИОННАЯ ДЕЯТЕЛЬНОСТЬ", None, None),
    ("Выручка со скидками", "1.0.", None),
    ("Выручка без скидок", "1.1.", None),
    ("Скидки", "1.2.", None),
    ("Общая сырьевая себестоимость", "2.1.", None),
    ("Валовая прибыль (маржа)", "3.1.", None),
    ("Операционные расходы", "2.0.", None),
    ("Аренда", "2.3.", None),
    ("постоянная часть", "2.3.1.", None),
    ("Операционная прибыль", "3.2.", None),
    ("Фонд учредителей", "2.9.", None),
]
MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def test_formula_parser():
    values = {"1": 10, "2": 3, "3": 1}
    for text, expected in (("1 - 2", 7), ("1 - (2 - 3)", 8), ("- 1 + 2", -7), ("1 − 2", 7),
                           ("1 - -2", 13)):
        assert evaluate(parse_formula(text), lambda c: values[c]) == expected, text
    for bad in ("1 +", "1 2", "(1", "1 )", "1 * 2", "+ + 1"):
        try:
            parse_formula(bad)
        except FormulaError:
            continue
        raise AssertionError(bad)


def report(client, env, **params):
    resp = client.get(f"{env['o']}/reports/management", headers=env["h"], params={**MARCH, **params})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["by_code"] = {r["code"]: r for r in data["rows"] if r["code"]}
    return data


def money_line(env, debit, credit, amount, debit_an, credit_an):
    return {"debit_account_id": env["acc"][debit], "credit_account_id": env["acc"][credit], "amount": amount,
            "debit": debit_an, "credit": credit_an}


def post(client, env, *postings):
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-03-10", "postings": list(postings)})
    assert resp.status_code == 201, resp.text


def test_management_report(client, env):
    assert report(client, env)["form_loaded"] is False
    result = load(client, env, preview(client, env, xlsx(FORM)))
    assert result["errors"] == []
    arts = {a["code"]: a["id"] for a in client.get(f"{env['o']}/articles", headers=env["h"]).json() if a["code"]}
    office, cp, contract = env["office"]["id"], env["cp"]["id"], env["contract"]["id"]
    sale = {"counterparty": cp, "contract": contract}
    post(client, env, money_line(env, "62.01", "90.01", "120000", sale, {"cfo": office, "pl_item": arts["1.1."]}))
    post(client, env, money_line(env, "90.01", "62.01", "5000", {"cfo": office, "pl_item": arts["1.2."]}, sale))
    post(client, env, money_line(env, "26", "60.01", "50000", {"cfo": office, "pl_item": arts["2.3.1."]}, sale))
    post(client, env, money_line(env, "75", "51", "10000", {"counterparty": cp},
                                 {"bank_account": env["bank_acc"]["id"], "cash_flow_item": arts["2.9."]}))

    data = report(client, env)
    rows = data["by_code"]
    fact = {code: r["values"]["total"]["fact"] for code, r in rows.items()}
    assert (fact["1.1"], fact["1.2"], fact["1.0"]) == ("120000.00", "5000.00", "115000.00")
    assert (fact["2.1"], fact["3.1"]) == ("0.00", "115000.00")
    assert (fact["2.3"], fact["2.3.1"], fact["2.9"]) == ("50000.00", "50000.00", "10000.00")
    assert rows["2.0"]["error"].startswith("нет строки с кодом 2.2")  # в нашей форме нет 2.2, 2.4…
    assert rows["3.2"]["error"] == ""  # 2.0 с ошибкой считается нулём
    assert rows["2.3.1"]["parent"] == rows["2.3"]["key"] and rows["2.3.1"]["level"] == 1
    assert data["rows"][0]["kind"] == "header" and data["rows"][0]["values"] == {}
    assert rows["1.2"]["invert"] is True

    # исправить формулу 2.0 — может только тот, кто правит справочники
    url = f"/api/report-form-rows/{rows['2.0']['id']}"
    assert client.put(url, headers=env["ceo"], json={"formula": "2.3"}).status_code == 403
    assert client.put(url, headers=env["h"], json={"formula": "2.3 +"}).status_code == 422
    assert client.put(f"/api/report-form-rows/{rows['2.3']['id']}", headers=env["h"],
                      json={"formula": "1.1"}).status_code == 422  # у статьи формулы нет
    assert client.put(url, headers=env["h"], json={"formula": "2.3"}).status_code == 200
    fact = {code: r["values"]["total"]["fact"] for code, r in report(client, env)["by_code"].items()}
    assert (fact["2.0"], fact["3.2"]) == ("50000.00", "65000.00")
    # формула по кругу — ошибка в строке, остальные считаются
    client.put(url, headers=env["h"], json={"formula": "3.2"})
    rows = report(client, env)["by_code"]
    assert "сама на себя" in rows["2.0"]["error"] and rows["1.0"]["values"]["total"]["fact"] == "115000.00"
    client.put(url, headers=env["h"], json={"formula": "2.3"})

    # расшифровка статьи — те же проводки
    drill = rows["2.9"]["drill"]
    lines = client.get(f"{env['o']}/reports/lines", headers=env["h"], params={
        **MARCH, "period": "turnover", "a_tree": drill["a_tree"], "account_id": drill["account_id"],
        "exclude_corr_account_id": drill["exclude_corr_ids"], "opening": "false"}).json()
    assert lines["credit"] == "10000.00"

    # повторная загрузка формы сохраняет исправленную формулу
    load(client, env, preview(client, env, xlsx(FORM)))
    assert report(client, env)["by_code"]["2.0"]["formula"] == "2.3"

    # план и факт: плана нет — нули, отклонение = факт
    data = report(client, env, mode="plan_fact")
    total = data["by_code"]["3.1"]["values"]["total"]
    assert (total["plan"], total["fact"], total["dev"]) == ("0.00", "115000.00", "115000.00")
    assert report(client, env, mode="plan")["by_code"]["3.1"]["values"]["total"]["fact"] is None
    resp = client.get(f"{env['o']}/reports/management.xlsx", headers=env["h"], params={**MARCH, "mode": "plan_fact"})
    assert resp.status_code == 200
    assert client.get(f"{env['o']}/reports/management", headers=env["other"], params=MARCH).status_code == 404
