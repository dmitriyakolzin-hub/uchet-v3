"""Ручная смена статуса заявок, шаг 22.7 (docs/TZ.md, раздел 30, «Детали 22.7»): администратор и финансовый
директор, причина в истории, та же автоматика, что у обычных действий. Данные вымышленные."""

import datetime as dt

from tests.test_asset_requests import act as asset_act
from tests.test_asset_requests import opex
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import create
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def set_status(client, h, kind, req, **body):
    return client.post(f"/api/{kind}/{req['id']}/status", headers=h, json=body)


def test_payment_request_status(client, env, db, make_user, headers_for):  # noqa: F811
    _, fd = member(db, make_user, headers_for, env, "fd2", "finance_director")
    _, fm = member(db, make_user, headers_for, env, "fm", "finance_manager")
    req = create(client, env).json()
    assert req["status"] == "draft"

    # только администратор и финансовый директор; причина обязательна
    assert set_status(client, fm, "payment-requests", req, status="approved", reason="x").status_code == 403
    assert client.get(f"/api/payment-requests/{req['id']}/status-options", headers=fm).status_code == 403
    assert set_status(client, fd, "payment-requests", req, status="approved", reason="").status_code == 422

    # «на согласовании» с выбранного шага маршрута
    options = client.get(f"/api/payment-requests/{req['id']}/status-options", headers=fd).json()
    assert options["route"][-1]["title"] == "Финансовый директор" and options["statuses"]["paid"] == "оплачена"
    last = options["route"][-1]["index"]
    assert set_status(client, fd, "payment-requests", req, status="pending", reason="x", step=99).status_code == 422
    resp = set_status(client, fd, "payment-requests", req, status="pending", reason="счёт принесли бумагой",
                      step=last)
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert (req["status"], req["step"], req["step_title"]) == ("pending", last, "Финансовый директор")

    # «оплачена» — как «Отметить оплату», затем обратно в черновик — как «Снять оплату»
    resp = set_status(client, env["h"], "payment-requests", req, status="paid", reason="оплачено наличными",
                      paid_on=TODAY.isoformat())
    assert resp.status_code == 200, resp.text
    assert (resp.json()["status"], resp.json()["paid_on"]) == ("paid", TODAY.isoformat())
    req = set_status(client, fd, "payment-requests", req, status="draft", reason="ошибка").json()
    assert (req["status"], req["paid_on"]) == ("draft", None)
    assert set_status(client, fd, "payment-requests", req, status="draft", reason="x").status_code == 422

    req = set_status(client, fd, "payment-requests", req, status="rejected", reason="дубль").json()
    assert (req["status"], req["reject_reason"]) == ("rejected", "дубль")
    events = client.get(f"/api/payment-requests/{req['id']}/events", headers=fd).json()
    manual = [e["comment"] for e in events if e["action"] == "status"]
    assert manual[0].startswith("черновик → на согласовании, с шага «Финансовый директор»")
    assert manual[-1] == "черновик → отклонена; причина: дубль"


def test_asset_request_status(client, env, db, make_user, headers_for):  # noqa: F811
    _, fd = member(db, make_user, headers_for, env, "fd2", "finance_director")
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    body = {"name": "Холодильник", "quantity": 1, "cfo_id": env["shop"]["id"], "estimated_price": "40000",
            "needed_on": TODAY.isoformat(), **opex(env)}
    req = client.post(f"{env['o']}/asset-requests", headers=engineer, json=body).json()
    url = f"/api/asset-requests/{req['id']}"
    assert set_status(client, engineer, "asset-requests", req, status="pending", reason="x").status_code == 403

    # без решения ФД в «решение принято» не перевести
    resp = set_status(client, fd, "asset-requests", req, status="approved", reason="x")
    assert resp.status_code == 422 and "Решения финансового директора ещё нет" in resp.json()["detail"]

    # «на согласовании» сразу на шаге ФД
    options = client.get(f"{url}/status-options", headers=fd).json()
    fd_step = next(s["index"] for s in options["route"] if s["title"] == "Финансовый директор")
    req = set_status(client, fd, "asset-requests", req, status="pending", reason="срочно", step=fd_step).json()
    assert req["status"] == "pending" and req["route"][fd_step]["state"] == "current"
    assert client.get(url, headers=engineer).json()["status"] == "pending"

    decision = {"model_name": "Бирюса", "legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"],
                "contract_id": env["contract"]["id"], "purchase_kind": "prepay", "price": "38000",
                "pay_date": TODAY.isoformat(), "bank_account_id": env["bank_acc"]["id"]}
    req = asset_act(client, fd, req, "approve", **decision).json()
    [pay] = req["payments"]

    # доставлено — как статус поставки; на доработку — оплаты убираются; обратно в решение — оплаты снова
    req = set_status(client, fd, "asset-requests", req, status="delivered", reason="привезли",
                     on=TODAY.isoformat()).json()
    assert (req["status"], req["delivered_on"]) == ("delivered", TODAY.isoformat())
    req = set_status(client, fd, "asset-requests", req, status="revision", reason="другая модель").json()
    assert (req["status"], req["payments"]) == ("revision", [])
    assert client.get(f"/api/payment-requests/{pay['id']}", headers=fd).status_code == 404
    req = set_status(client, fd, "asset-requests", req, status="approved", reason="оставили как было").json()
    assert req["status"] == "approved" and len(req["payments"]) == 1

    # «принято» — только приёмкой; после приёмки статус вручную не меняется
    resp = set_status(client, fd, "asset-requests", req, status="received", reason="x")
    assert resp.status_code == 422 and "приёмка" in resp.json()["detail"]
    resp = asset_act(client, fd, req, "receive", received_on=TODAY.isoformat(), quantity=1, employee_id=None,
                     cfo_id=env["shop"]["id"], warehouse_id=None, mol_id=None, status="stock", discrepancy="")
    assert resp.status_code == 200, resp.text
    assert set_status(client, fd, "asset-requests", req, status="cancelled", reason="x").status_code == 422

    events = client.get(f"{url}/events", headers=fd).json()
    manual = [e["details"] for e in events if e["action"] == "status"]
    assert manual[0].startswith("Черновик → На согласовании, с шага «Финансовый директор»")
    assert manual[2] == "Доставлено → На доработке; оплаты убраны из календаря; причина: другая модель"
