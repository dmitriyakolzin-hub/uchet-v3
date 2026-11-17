"""Выбытие имущества, шаг 19.6: продажа, списание, гибель / утрата с согласованием ФД; основные средства — через
продажу и списание ОС, остальное — выручка без списания стоимости (docs/TZ.md, раздел 28, «Детали 19.6»)."""

import datetime as dt

from tests.test_asset_accounting import received
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def test_disposal(client, env, db, make_user, headers_for):  # noqa: F811
    from app.models import FixedAsset, Operation

    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    _, buh = member(db, make_user, headers_for, env, "buh", "accountant")
    url = f"{env['o']}/asset-disposals"

    # основное средство: принято, поставлено на учёт и введено в эксплуатацию
    [oven] = received(client, env, aho, name="Печь", quantity=1, price="300000")
    resp = client.post(f"{env['o']}/asset-items/account", headers=buh, json={
        "ids": [oven["id"]], "kind": "fixed_asset", "on_date": TODAY.isoformat(), "counterparty_id": env["cp"]["id"],
        "useful_life_months": 60, "commission_on": TODAY.isoformat()})
    assert resp.status_code == 200, resp.text
    # малоценка без учёта
    chairs = client.post(f"{env['o']}/asset-items", headers=aho, json={
        "name": "Стул", "count": 2, "cfo_id": env["office"]["id"], "legal_entity_id": env["le"].id,
        "purchase_price": "900"}).json()

    body = {"ids": [chairs[0]["id"]], "kind": "writeoff", "on_date": TODAY.isoformat(), "reason": ""}
    assert client.post(url, headers=env["head"], json={**body, "reason": "x"}).status_code == 403
    assert client.post(url, headers=engineer, json=body).status_code == 422  # причина обязательна
    resp = client.post(url, headers=engineer, json={**body, "reason": "сломан"})
    assert resp.status_code == 201, resp.text
    act = resp.json()
    assert (act["number"], act["status"], act["can_decide"], act["can_cancel"]) == (1, "pending", False, True)
    assert client.get(f"/api/asset-items/{chairs[0]['id']}", headers=aho).json()["in_disposal"] is True
    assert client.post(url, headers=aho, json={**body, "reason": "ещё раз"}).status_code == 422  # уже на согласовании
    assert client.post(f"/api/asset-disposals/{act['id']}/approve", headers=engineer).status_code == 403
    act = client.post(f"/api/asset-disposals/{act['id']}/approve", headers=env["h"]).json()
    assert act["status"] == "done" and act["items"][0]["operation_number"] is None  # без проводок
    item = client.get(f"/api/asset-items/{chairs[0]['id']}", headers=aho).json()
    assert item["status"] == "disposed"
    assert client.get(f"/api/asset-items/{chairs[0]['id']}/events", headers=aho).json()[-1]["action"] == "disposed"
    # отмена выбытия — прежний статус
    act = client.post(f"/api/asset-disposals/{act['id']}/undo", headers=env["h"]).json()
    assert act["status"] == "undone"
    assert client.get(f"/api/asset-items/{chairs[0]['id']}", headers=aho).json()["status"] == "stock"

    # продажа: цена каждой единицы и покупатель; ОС — через продажу ОС, стул — выручка на 91.01
    sale = {"ids": [oven["id"], chairs[1]["id"]], "kind": "sale", "on_date": TODAY.isoformat(),
            "counterparty_id": env["cp"]["id"], "vat_rate": 20, "prices": {str(oven["id"]): "240000"}}
    assert client.post(url, headers=aho, json=sale).status_code == 422  # нет цены стула
    sale["prices"][str(chairs[1]["id"])] = "600"
    act = client.post(url, headers=aho, json=sale).json()
    assert act["total"] == "240600.00"
    resp = client.post(f"/api/asset-disposals/{act['id']}/approve", headers=env["h"])
    assert resp.status_code == 200, resp.text
    act = resp.json()
    db.expire_all()
    fa = db.get(FixedAsset, client.get(f"/api/asset-items/{oven['id']}", headers=aho).json()["fixed_asset_id"])
    assert (fa.disposed_on, fa.disposal_kind) == (TODAY, "sale")
    chair_line = next(x for x in act["items"] if x["id"] == chairs[1]["id"])
    op = db.get(Operation, db.query(Operation.id).filter(Operation.number == chair_line["operation_number"],
                                                         Operation.organization_id == env["org"].id).scalar())
    assert op.status == "posted" and len(op.postings) == 2  # выручка и НДС
    # отмена продажи — карточка ОС снова в эксплуатации, операция стула удалена
    client.post(f"/api/asset-disposals/{act['id']}/undo", headers=env["h"])
    db.expire_all()
    assert db.get(FixedAsset, fa.id).disposed_on is None
    assert client.get(f"/api/asset-items/{oven['id']}", headers=aho).json()["status"] == "in_use"

    # гибель / утрата с виновным лицом — отклонение и отмена автором
    person = env["post"]("employees", {"full_name": "Растеряев Роман"})
    loss = client.post(url, headers=aho, json={"ids": [chairs[1]["id"]], "kind": "loss", "on_date": TODAY.isoformat(),
                                                "reason": "пропал", "employee_id": person["id"]}).json()
    assert loss["employee_name"] == "Растеряев Роман"
    loss = client.post(f"/api/asset-disposals/{loss['id']}/reject", headers=env["h"], json={"reason": "найти"}).json()
    assert (loss["status"], loss["reject_reason"]) == ("rejected", "найти")
    again = client.post(url, headers=aho, json={"ids": [chairs[1]["id"]], "kind": "loss", "on_date": TODAY.isoformat(),
                                                 "reason": "пропал"}).json()
    assert client.post(f"/api/asset-disposals/{again['id']}/cancel", headers=engineer).status_code == 403
    assert client.post(f"/api/asset-disposals/{again['id']}/cancel", headers=aho).json()["status"] == "cancelled"
    listed = client.get(url, headers=engineer, params={"status": "rejected,cancelled"}).json()
    assert {x["number"] for x in listed} == {loss["number"], again["number"]}
    assert client.get(url, headers=env["other"]).status_code == 404
