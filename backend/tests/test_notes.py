"""Примечания к цифрам отчётов и к операциям, метки (docs/TZ.md, раздел 23)."""

from tests.test_plan_fact import env  # noqa: F401
from tests.test_plan_grid import env as grid_env  # noqa: F401
from tests.test_users import admin  # noqa: F401


def note(client, env, h=None, **body):
    data = {"report": "pl", "row_key": f"art:{env['art']['Аренда']}", "row_title": "Аренда", "column_key": "2027-01",
            "column_title": "Январь 2027", "text": "Проверить договор", **body}
    return client.post(f"{env['o']}/notes", headers=h or env["h"], json=data)


def test_labels_and_report_notes(client, env):
    o, h = env["o"], env["h"]
    label = client.post(f"{o}/note-labels", headers=h, json={"name": "Проверить", "color": "#d92d20"}).json()
    assert client.post(f"{o}/note-labels", headers=h, json={"name": "Плохой цвет", "color": "red"}).status_code == 422
    assert client.post(f"{o}/note-labels", headers=env["ceo"], json={"name": "CEO"}).status_code == 403
    assert [x["name"] for x in client.get(f"{o}/note-labels", headers=env["ceo"]).json()] == ["Проверить"]

    # пишут все, кто видит отчёты, в том числе CEO
    first = note(client, env, label_id=label["id"], cfo_id=env["shop"]["id"]).json()
    assert (first["status"], first["label_id"], first["can_edit"]) == ("open", label["id"], True)
    ceo_note = note(client, env, h=env["ceo"], text="Почему выросло?", column_key="total", column_title="Итого",
                    date_from="2027-01-01", date_to="2027-02-28").json()
    assert ceo_note["author"] and ceo_note["can_edit"] is True

    listed = client.get(f"{o}/notes", headers=h, params={"report": "pl"}).json()
    assert [n["id"] for n in listed] == [ceo_note["id"], first["id"]]  # новые — сверху
    assert client.get(f"{o}/notes", headers=h, params={"label_id": label["id"]}).json()[0]["id"] == first["id"]
    assert [n["id"] for n in client.get(f"{o}/notes", headers=env["ceo"], params={"mine": True}).json()] == [ceo_note["id"]]

    # руководитель ЦФО (ветка «Торговля»): видит примечание магазина, не видит без ЦФО; его примечание — в ветке
    head = client.get(f"{o}/notes", headers=env["head"]).json()
    assert [n["id"] for n in head] == [first["id"]] and head[0]["can_edit"] is False
    own = note(client, env, h=env["head"], text="Согласовано").json()
    assert own["cfo_id"] == env["trade"]["id"]  # ЦФО не указан — ставится корень ветки
    assert note(client, env, h=env["head"], cfo_id=env["logistics"]["id"]).status_code == 404

    # менять текст — автор, админ и фин. директор; «решено» — любой, кто видит
    url = f"/api/notes/{first['id']}"
    assert client.patch(url, headers=env["ceo"], json={"text": "Чужой"}).status_code == 403
    resolved = client.patch(url, headers=env["head"], json={"status": "resolved"}).json()
    assert resolved["status"] == "resolved" and resolved["resolved_at"]
    assert client.get(f"{o}/notes", headers=h, params={"status": "open"}).json()[0]["id"] != first["id"]
    assert client.patch(url, headers=h, json={"clear_label": True, "text": "Договор в порядке"}).json()["label_id"] is None
    assert client.delete(url, headers=env["ceo"]).status_code == 403
    assert client.delete(url, headers=h).status_code == 204
    assert client.patch(url, headers=h, json={"status": "open"}).status_code == 404


def test_operation_notes(client, env):
    o, h = env["o"], env["h"]
    ops = client.get(f"{o}/operations", headers=h).json()["items"]
    op = ops[0]
    created = client.post(f"{o}/notes", headers=env["ceo"], json={
        "report": "operation", "operation_id": op["id"], "text": "Нет первичного документа"}).json()
    assert (created["row_title"], created["operation_number"]) == (f"Операция № {op['number']}", op["number"])
    assert client.get(f"{o}/notes", headers=h, params={"operation_id": op["id"]}).json()[0]["id"] == created["id"]
    # руководитель ЦФО видит: операция по магазину его ветки
    assert [n["id"] for n in client.get(f"{o}/notes", headers=env["head"]).json()] == [created["id"]]
    # операция без номера / чужая — нельзя
    assert client.post(f"{o}/notes", headers=h, json={"report": "operation", "text": "x"}).status_code == 422
    assert client.post(f"{o}/notes", headers=h, json={"report": "operation", "operation_id": 10**9,
                                                       "text": "x"}).status_code == 404
