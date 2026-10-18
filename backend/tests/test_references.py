import pytest

SBER = {"bic": "044525225", "name": "ПАО Сбербанк", "corr_account": "30101810400000000225", "city": "Москва"}


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import Currency, LegalEntity

    admin = make_user(login="admin", is_admin=True)
    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    other_le = LegalEntity(organization_id=other.id, full_name="ООО Б", short_name="Б", inn="7709876543")
    db.add_all([le, other_le])
    db.commit()
    bank = client.post("/api/banks", headers=headers_for(admin), json=SBER).json()
    rub = db.query(Currency).filter_by(code="RUB").one()
    return {"admin": admin, "fd": fd, "ceo": ceo, "other_fd": other_fd, "org": org, "other": other,
            "le": le, "other_le": other_le, "bank": bank, "rub": rub, "h": headers_for}


def _url(env, path, org=None):
    return f"/api/organizations/{(org or env['org']).id}/{path}"


# --- Банки ---


def test_banks_admin_only(client, env):
    h_fd = env["h"](env["fd"])
    assert client.get("/api/banks", headers=h_fd).json()[0]["bic"] == "044525225"
    assert client.post("/api/banks", headers=h_fd, json={**SBER, "bic": "044525974"}).status_code == 403
    assert client.patch(f"/api/banks/{env['bank']['id']}", headers=h_fd, json={"name": "x"}).status_code == 403


def test_bank_validation_and_duplicate(client, env):
    h = env["h"](env["admin"])
    assert client.post("/api/banks", headers=h, json=SBER).status_code == 409
    assert client.post("/api/banks", headers=h, json={**SBER, "bic": "12345"}).status_code == 422
    assert client.post("/api/banks", headers=h,
                       json={**SBER, "bic": "044525974", "corr_account": "123"}).status_code == 422


# --- Расчётные счета ---


def _account(env, **over):
    body = {"legal_entity_id": env["le"].id, "bank_id": env["bank"]["id"], "currency_id": env["rub"].id,
            "number": "40702810900000012345", "name": "Сбер, основной"}
    body.update(over)
    return body


def test_fd_creates_bank_account(client, env):
    h = env["h"](env["fd"])
    resp = client.post(_url(env, "bank-accounts"), headers=h, json=_account(env))
    assert resp.status_code == 201, resp.text
    assert resp.json()["organization_id"] == env["org"].id
    assert client.post(_url(env, "bank-accounts"), headers=h, json=_account(env)).status_code == 409
    listed = client.get(_url(env, "bank-accounts"), headers=env["h"](env["ceo"])).json()
    assert [a["name"] for a in listed] == ["Сбер, основной"]


def test_bank_account_bad_number(client, env):
    resp = client.post(_url(env, "bank-accounts"), headers=env["h"](env["fd"]),
                       json=_account(env, number="4070281090000001234"))
    assert resp.status_code == 422


def test_foreign_legal_entity_rejected(client, env):
    """Нельзя завести счёт на юрлицо чужой организации."""
    resp = client.post(_url(env, "bank-accounts"), headers=env["h"](env["fd"]),
                       json=_account(env, legal_entity_id=env["other_le"].id))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Юрлицо: не найдено в этой организации"


def test_unknown_bank_rejected(client, env):
    resp = client.post(_url(env, "bank-accounts"), headers=env["h"](env["fd"]), json=_account(env, bank_id=999))
    assert resp.status_code == 422 and resp.json()["detail"] == "Банк: не найдено"


def test_ceo_cannot_create_or_edit(client, env):
    created = client.post(_url(env, "bank-accounts"), headers=env["h"](env["fd"]), json=_account(env)).json()
    h = env["h"](env["ceo"])
    assert client.post(_url(env, "bank-accounts"), headers=h,
                       json=_account(env, number="40702810900000099999")).status_code == 403
    assert client.patch(f"/api/bank-accounts/{created['id']}", headers=h, json={"name": "x"}).status_code == 403


def test_other_org_cannot_see_or_edit(client, env):
    created = client.post(_url(env, "bank-accounts"), headers=env["h"](env["fd"]), json=_account(env)).json()
    h = env["h"](env["other_fd"])
    assert client.get(_url(env, "bank-accounts"), headers=h).status_code == 404
    assert client.patch(f"/api/bank-accounts/{created['id']}", headers=h, json={"name": "x"}).status_code == 404


def test_archive_bank_account(client, env):
    h = env["h"](env["fd"])
    created = client.post(_url(env, "bank-accounts"), headers=h, json=_account(env)).json()
    resp = client.patch(f"/api/bank-accounts/{created['id']}", headers=h, json={"is_active": False})
    assert resp.json()["is_active"] is False


# --- Зарплатные проекты ---


def test_salary_project(client, env):
    h = env["h"](env["fd"])
    resp = client.post(_url(env, "salary-projects"), headers=h,
                       json={"legal_entity_id": env["le"].id, "bank_id": env["bank"]["id"], "name": "Сбер ЗП"})
    assert resp.status_code == 201
    assert client.get(_url(env, "salary-projects"), headers=h).json()[0]["name"] == "Сбер ЗП"


# --- Контрагенты ---


@pytest.mark.parametrize("body,ok", [
    ({"name": "Ромашка", "kind": "legal", "inn": "7701111111", "kpp": "770101001"}, True),
    ({"name": "ИП Иванов", "kind": "entrepreneur", "inn": "770111111111"}, True),
    ({"name": "Петров П.П.", "kind": "person"}, True),
    ({"name": "Петров П.П.", "kind": "person", "inn": "770111111111"}, True),
    ({"name": "Ромашка", "kind": "legal"}, False),                         # без ИНН
    ({"name": "Ромашка", "kind": "legal", "inn": "770111111111"}, False),  # 12 цифр у организации
    ({"name": "ИП", "kind": "entrepreneur", "inn": "7701111111"}, False),
    ({"name": "Петров", "kind": "person", "inn": "7701111111"}, False),
    ({"name": "X", "kind": "bank", "inn": "7701111111"}, False),
])
def test_counterparty_inn_by_kind(client, env, body, ok):
    resp = client.post(_url(env, "counterparties"), headers=env["h"](env["fd"]), json=body)
    assert resp.status_code == (201 if ok else 422), resp.text


def test_counterparty_inn_unique_in_org_only(client, env):
    body = {"name": "Ромашка", "kind": "legal", "inn": "7701111111", "is_supplier": True}
    assert client.post(_url(env, "counterparties"), headers=env["h"](env["fd"]), json=body).status_code == 201
    assert client.post(_url(env, "counterparties"), headers=env["h"](env["fd"]), json=body).status_code == 409
    other = client.post(_url(env, "counterparties", env["other"]), headers=env["h"](env["other_fd"]), json=body)
    assert other.status_code == 201  # в другой организации — свой контрагент


def test_two_persons_without_inn(client, env):
    h = env["h"](env["fd"])
    for name in ("Петров", "Сидоров"):
        assert client.post(_url(env, "counterparties"), headers=h, json={"name": name, "kind": "person"}).status_code == 201


def test_counterparty_update_keeps_inn_rules(client, env):
    h = env["h"](env["fd"])
    created = client.post(_url(env, "counterparties"), headers=h,
                          json={"name": "Ромашка", "kind": "legal", "inn": "7701111111"}).json()
    # сменить вид на ИП, не сменив ИНН, нельзя
    resp = client.patch(f"/api/counterparties/{created['id']}", headers=h, json={"kind": "entrepreneur"})
    assert resp.status_code == 422
    resp = client.patch(f"/api/counterparties/{created['id']}", headers=h,
                        json={"kind": "entrepreneur", "inn": "770111111111", "kpp": None})
    assert resp.status_code == 200 and resp.json()["kpp"] is None


def test_counterparties_isolated_between_orgs(client, env):
    client.post(_url(env, "counterparties"), headers=env["h"](env["fd"]),
                json={"name": "Секретный поставщик", "kind": "legal", "inn": "7701111111"})
    assert client.get(_url(env, "counterparties"), headers=env["h"](env["other_fd"])).status_code == 404
    mine = client.get(_url(env, "counterparties", env["other"]), headers=env["h"](env["other_fd"])).json()
    assert mine == []


# --- Договоры ---


def _counterparty(client, env, org=None, who="fd"):
    return client.post(_url(env, "counterparties", org), headers=env["h"](env[who]),
                       json={"name": "Поставщик", "kind": "legal", "inn": "7702222222"}).json()


def test_contract(client, env):
    cp = _counterparty(client, env)
    h = env["h"](env["fd"])
    body = {"counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": env["rub"].id,
            "kind": "supplier", "number": "15/2026", "contract_date": "2026-01-15"}
    resp = client.post(_url(env, "contracts"), headers=h, json=body)
    assert resp.status_code == 201, resp.text
    assert client.post(_url(env, "contracts"), headers=h, json=body).status_code == 201  # номер может повторяться
    resp = client.patch(f"/api/contracts/{resp.json()['id']}", headers=h, json={"contract_date": None})
    assert resp.json()["contract_date"] is None


def test_contract_with_foreign_counterparty_rejected(client, env):
    foreign_cp = _counterparty(client, env, org=env["other"], who="other_fd")
    body = {"counterparty_id": foreign_cp["id"], "legal_entity_id": env["le"].id, "currency_id": env["rub"].id,
            "kind": "supplier", "number": "1"}
    resp = client.post(_url(env, "contracts"), headers=env["h"](env["fd"]), json=body)
    assert resp.status_code == 422 and "Контрагент" in resp.json()["detail"]


def test_contract_cannot_be_moved_to_foreign_entity(client, env):
    cp = _counterparty(client, env)
    h = env["h"](env["fd"])
    created = client.post(_url(env, "contracts"), headers=h, json={
        "counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": env["rub"].id,
        "kind": "supplier", "number": "1"}).json()
    resp = client.patch(f"/api/contracts/{created['id']}", headers=h, json={"legal_entity_id": env["other_le"].id})
    assert resp.status_code == 422
