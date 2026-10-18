"""Проверка интерфейса в браузере (для разработки): вход, организации, юрлица, пользователи, права CEO.

Запуск на пустой базе с администратором admin / admin-pass-123 и приложением на 127.0.0.1:8011:
    .venv/bin/python scripts/ui_smoke.py <папка для скриншотов>
Нужен пакет playwright и Chromium (в облачной среде: /opt/pw-browsers/chromium).
"""
import os
import sys
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("UI_BASE", "http://127.0.0.1:8011")
OUT = sys.argv[1]
errors = []

def login(page, user, pwd):
    page.goto(BASE + "/")
    page.fill("input[name=login]", user)
    page.fill("input[name=password]", pwd)
    page.click("#login-form button")
    expect(page.locator("#app-view")).to_be_visible()

def dialog_fill(page, values):
    d = page.locator("dialog[open]")
    for name, value in values.items():
        loc = d.locator(f"[name={name}]")
        if loc.evaluate("e => e.tagName") == "SELECT":
            loc.select_option(value)
        else:
            loc.fill(value)
    return d

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"))
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    login(page, "admin", "admin-pass-123")
    expect(page.locator("#nav a")).to_have_count(7)

    # организация
    page.click("text=Добавить организацию")
    d = dialog_fill(page, {"name": "Группа АВМВ", "kind": "group", "note": "Основная группа"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Группа АВМВ")).to_be_visible()
    page.click("text=Добавить организацию")
    d = dialog_fill(page, {"name": "Клиент <b>Икс</b>", "kind": "client"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Клиент <b>Икс</b>")).to_be_visible()

    # юрлицо: сначала с ошибкой ИНН, потом правильно
    page.locator(".tabulator-row", has_text="Группа АВМВ").locator(".tabulator-cell").first.click()
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()
    page.click("text=Добавить юрлицо")
    d = dialog_fill(page, {"full_name": "ООО «Ромашка»", "short_name": "Ромашка", "inn": "123"})
    d.locator("button[type=submit]").click()
    expect(d.locator(".error")).to_contain_text("inn")
    dialog_fill(page, {"inn": "7701234567", "kpp": "770101001"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    page.screenshot(path=f"{OUT}/1_organizations.png", full_page=True)

    # план счетов: дерево, добавление субсчёта, смена аналитики
    page.click("#nav a[data-page=accounts]")
    row60 = page.locator(".tabulator-row", has_text="Расчёты с поставщиками и подрядчиками").first
    expect(row60).to_contain_text("Контрагент, Договор")
    expect(page.locator(".tabulator-row", has_text="Авансы выданные")).to_have_count(0)  # свёрнуто
    row60.locator(".tabulator-data-tree-control").click()
    expect(page.locator(".tabulator-row", has_text="Авансы выданные")).to_be_visible()
    page.click("text=Добавить счёт или субсчёт")
    d = dialog_fill(page, {"code": "76.05", "name": "Расчёты с арендодателями"})
    d.locator("button[type=submit]").click()
    row76 = page.locator(".tabulator-row", has_text="Расчёты с разными дебиторами").first
    row76.locator(".tabulator-data-tree-control").click()
    sub = page.locator(".tabulator-row", has_text="Расчёты с арендодателями")
    expect(sub).to_contain_text("Контрагент, Договор")  # унаследовал от 76
    sub.locator(".tabulator-cell[tabulator-field=_edit]").click()
    d = page.locator("dialog[open]")
    d.locator("label.check", has_text="ЦФО").locator("input").check()
    d.locator("button[type=submit]").click()
    expect(sub).to_contain_text("Контрагент, Договор, ЦФО")
    page.screenshot(path=f"{OUT}/6_accounts.png", full_page=True)

    # валюта и курс (с запятой, как привычно)
    page.click("#nav a[data-page=currencies]")
    expect(page.locator(".tabulator-row", has_text="Российский рубль")).to_be_visible()
    page.click("text=Добавить валюту")
    d = dialog_fill(page, {"code": "usd", "numeric_code": "840", "name": "Доллар США"})
    d.locator("button[type=submit]").click()
    expect(page.locator("h2", has_text="Курсы: USD")).to_be_visible()
    page.click("text=Добавить курс")
    d = dialog_fill(page, {"rate_date": "2026-10-01", "rate": "81,5432"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="81,5432")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="01.10.2026")).to_be_visible()
    page.screenshot(path=f"{OUT}/5_currencies.png", full_page=True)
    page.click("#nav a[data-page=organizations]")

    # банк (общий справочник) и справочники организации
    page.click("#nav a[data-page=common]")
    page.click("button.tab:has-text('Единицы измерения')")
    expect(page.locator(".tabulator-row", has_text="Штука")).to_be_visible()
    page.click("button.tab:has-text('Банки')")
    page.click("text=Добавить банк")
    d = dialog_fill(page, {"bic": "044525225", "name": "ПАО Сбербанк", "corr_account": "30101810400000000225"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="ПАО Сбербанк")).to_be_visible()

    page.click("#nav a[data-page=references]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.click("button.tab:has-text('Расчётные счета')")
    page.click("text=Добавить расчётный счёт")
    d = page.locator("dialog[open]")
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    d.locator("[name=bank_id]").select_option(label="ПАО Сбербанк (044525225)")
    dialog_fill(page, {"name": "Сбер, основной", "number": "40702810900000012345"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="Сбер, основной")
    expect(row).to_contain_text("ПАО Сбербанк")
    expect(row).to_contain_text("RUB")  # валюта по умолчанию — рубль

    page.click("button.tab:has-text('Контрагенты')")
    page.click("text=Добавить контрагента")
    d = dialog_fill(page, {"name": "Поставщик Плюс", "inn": "770111111"})  # 9 цифр — ошибка
    d.locator("label.check", has_text="Поставщик").locator("input").check()
    d.locator("button[type=submit]").click()
    expect(d.locator(".error")).not_to_be_empty()
    dialog_fill(page, {"inn": "7701111111"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_contain_text("7701111111")

    page.click("button.tab:has-text('Договоры')")
    page.click("text=Добавить договор")
    d = page.locator("dialog[open]")
    d.locator("[name=counterparty_id]").select_option(label="Поставщик Плюс (ИНН 7701111111)")
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    dialog_fill(page, {"number": "15/2026", "contract_date": "2026-01-15"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="15/2026")
    expect(row).to_contain_text("Поставщик Плюс")
    expect(row).to_contain_text("15.01.2026")
    page.screenshot(path=f"{OUT}/7_contracts.png", full_page=True)

    # дерево ЦФО: компания → направление → филиал, затем сотрудник в филиале
    page.click("button.tab:has-text('ЦФО')")
    page.click("text=Добавить ЦФО уровня «Компания»")
    d = dialog_fill(page, {"name": "АВМВ"})
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    d.locator("button[type=submit]").click()
    page.locator(".tabulator-row", has_text="АВМВ").locator(".tabulator-cell[tabulator-field=_add]").click()
    d = dialog_fill(page, {"name": "Торговля"})
    expect(d.locator("[name=parent_id] option:checked")).to_have_text("АВМВ")  # родитель подставлен
    d.locator("button[type=submit]").click()
    page.locator(".tabulator-row", has_text="Торговля").locator(".tabulator-cell[tabulator-field=_add]").click()
    d = dialog_fill(page, {"name": "Филиал Юг"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="Филиал Юг")
    expect(row).to_contain_text("Филиал")
    expect(row.locator(".tabulator-cell[tabulator-field=_add]")).to_have_text("+ магазин")
    page.screenshot(path=f"{OUT}/8_cfos.png", full_page=True)

    page.click("button.tab:has-text('Склады')")
    page.click("text=Добавить склад")
    d = dialog_fill(page, {"name": "Склад Юг"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Склад Юг")).to_contain_text("Филиал Юг")

    page.click("button.tab:has-text('Номенклатура')")
    page.click("text=Добавить номенклатуру")
    d = dialog_fill(page, {"name": "Кофе зерновой", "article": "CF-001"})
    expect(d.locator("[name=unit_id] option:checked")).to_have_text("шт — Штука")  # по умолчанию
    d.locator("[name=unit_id]").select_option(label="кг — Килограмм")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Кофе зерновой")).to_contain_text("кг")

    page.click("button.tab:has-text('Сотрудники')")
    page.click("text=Добавить сотрудника")
    d = dialog_fill(page, {"full_name": "Иванов Иван", "personnel_number": "0001", "position": "Продавец",
                           "hired_on": "2026-01-10"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Иванов Иван")).to_contain_text("АВМВ / Торговля / Филиал Юг")

    # пользователь CEO
    page.click("#nav a[data-page=users]")
    page.click("text=Добавить пользователя")
    d = dialog_fill(page, {"login": "ceo", "full_name": "Сидоров С.С.", "password": "ceo-password-1"})
    d.locator("select[aria-label='Роль в Группа АВМВ']").select_option("ceo")
    expect(d).not_to_contain_text("null")
    page.screenshot(path=f"{OUT}/2_user_form.png")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Группа АВМВ: CEO")).to_be_visible()
    page.screenshot(path=f"{OUT}/3_users.png", full_page=True)

    # вход под CEO
    page.click("#logout")
    login(page, "ceo", "ceo-password-1")
    expect(page.locator("#nav a")).to_have_count(6)  # нет «Пользователи»
    expect(page.locator("text=Добавить организацию")).to_have_count(0)
    expect(page.locator(".tabulator").first.locator(".tabulator-row")).to_have_count(1)  # видит только свою
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()  # выбрана автоматически
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator("text=Добавить юрлицо")).to_be_hidden()
    page.goto(BASE + "/#/users")  # прямой переход в чужой раздел
    expect(page.locator("#nav a.active")).to_have_text("Организации")
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]", has_text="✎")).to_have_count(0)
    page.screenshot(path=f"{OUT}/4_ceo.png", full_page=True)

    # CEO видит справочники организации без кнопок изменения
    page.click("#nav a[data-page=references]")
    page.click("button.tab:has-text('Контрагенты')")
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_be_visible()
    expect(page.locator("text=Добавить контрагента")).to_have_count(0)
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]")).to_have_count(0)

    # CEO видит план счетов, но не может его менять
    page.click("#nav a[data-page=accounts]")
    expect(page.locator(".tabulator-row", has_text="Касса")).to_be_visible()
    expect(page.locator("text=Добавить счёт или субсчёт")).to_have_count(0)
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]")).to_have_count(0)

    # CEO видит валюты и курсы, но не может их менять
    page.click("#nav a[data-page=currencies]")
    page.locator(".tabulator-row", has_text="Доллар США").locator(".tabulator-cell").first.click()
    expect(page.locator(".tabulator-row", has_text="81,5432")).to_be_visible()
    expect(page.locator("text=Добавить валюту")).to_have_count(0)
    expect(page.locator("text=Добавить курс")).to_be_hidden()
    expect(page.locator(".tabulator-cell[tabulator-field=_delete]")).to_have_count(0)

    # смена пароля
    page.click("#nav a[data-page=password]")
    page.fill("#cur", "ceo-password-1"); page.fill("#new1", "ceo-password-2"); page.fill("#new2", "ceo-password-2")
    page.click("text=Сменить пароль >> nth=-1")
    expect(page.locator(".toast")).to_have_text("Пароль изменён")
    page.reload()
    expect(page.locator("#app-view")).to_be_visible()  # остаётся в системе с новыми токенами
    browser.close()

print("ОШИБКИ В КОНСОЛИ:", errors or "нет")
print("UI-ПРОВЕРКА ПРОЙДЕНА")
