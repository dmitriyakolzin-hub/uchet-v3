"""Проверка интерфейса в браузере (для разработки): вход, организации, юрлица, пользователи, права CEO.

Запуск на пустой базе с администратором admin / admin-pass-123 и приложением на 127.0.0.1:8011:
    .venv/bin/python scripts/ui_smoke.py <папка для скриншотов>
Нужен пакет playwright и Chromium (в облачной среде: /opt/pw-browsers/chromium).
"""
import datetime as dt
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
    expect(page.locator("#nav a")).to_have_count(17)
    expect(page.locator("#nav a.active")).to_have_text("Журнал операций")  # открывается первым
    page.click("#nav a[data-page=organizations]")

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

    # статьи: стартовый набор, фильтр ДДС/ОПиУ, новая статья с шаблоном
    page.click("#nav a[data-page=articles]")
    row = page.locator(".tabulator-row", has_text="Оплата поставщикам")
    expect(row).to_contain_text("Операционная · Выплата")
    expect(row).to_contain_text("Дт 60.01 — Кт 51")
    page.click("button.tab:has-text('ОПиУ')")
    expect(page.locator(".tabulator-row", has_text="Оплата поставщикам")).to_have_count(0)
    expect(page.locator(".tabulator-row", has_text="Выручка от продаж")).to_be_visible()
    page.click("text=Добавить статью")
    d = dialog_fill(page, {"name": "Охрана"})
    d.locator("[name=cash_flow_activity]").select_option(label="Операционная")
    d.locator("[name=cash_flow_direction]").select_option(label="Выплата")
    d.locator("[name=debit_account_id]").select_option(label="60.01 Расчёты с поставщиками и подрядчиками")
    d.locator("[name=credit_account_id]").select_option(label="51 Расчётные счета")
    d.locator("button[type=submit]").click()
    page.click("button.tab:has-text('ДДС')")
    expect(page.locator(".tabulator-row", has_text="Охрана")).to_contain_text("Дт 60.01 — Кт 51")
    page.screenshot(path=f"{OUT}/9_articles.png", full_page=True)

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
    d = dialog_fill(page, {"full_name": "Иванов Иван", "personnel_number": "0001", "internal_number": "M-1",
                           "position": "Продавец", "hired_on": "2026-01-10"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("[name=pl_item_id]").select_option(label="Оплата труда — коммерческий персонал")
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    d.locator("button[type=submit]").click()
    emp = page.locator(".tabulator-row", has_text="Иванов Иван")
    expect(emp).to_contain_text("АВМВ / Торговля / Филиал Юг")
    expect(emp).to_contain_text("Оплата труда — коммерческий персонал")
    # оклады: история с датами
    emp.locator(".tabulator-cell[tabulator-field=_salary]").click()
    sal = page.locator("dialog[open]", has_text="Оклады: Иванов Иван")
    sal.locator("button", has_text="Новый оклад").click()
    d = page.locator("dialog[open]", has_text="Действует с")
    d.locator("[name=valid_from]").fill("2026-01-10")
    d.locator("[name=amount]").fill("85 000")
    d.locator("button[type=submit]").click()
    expect(sal.locator(".tabulator-row")).to_contain_text("85 000,00")
    sal.locator("button", has_text="Закрыть").click()
    expect(emp).to_contain_text("M-1")
    # загрузка сотрудников из Excel (без юрлица — в начисление зарплаты не попадёт)
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["ФИО", "Табельный №", "Внутренний №", "Должность"])
    wb.active.append(["Петров Пётр", "0002", "M-2", "Кассир"])
    wb.save(f"{OUT}/employees.xlsx")
    page.locator("input[aria-label='Файл сотрудников']").set_input_files(f"{OUT}/employees.xlsx")
    d = page.locator("dialog[open]", has_text="Сотрудники загружены")
    expect(d).to_contain_text("Добавлено: 1")
    d.locator("button", has_text="Понятно").click()
    expect(page.locator(".tabulator-row", has_text="Петров Пётр")).to_contain_text("M-2")

    # журнал: операция по статье с шаблоном, проведение, неполный черновик, массовое проведение
    page.click("#nav a[data-page=journal]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.click("text=Новая операция")
    d = page.locator("dialog[open]")
    d.locator("[name=article_id]").select_option(label="Оплата поставщикам")
    expect(d.locator("[name=debit_account] option:checked")).to_have_text("60.01 Расчёты с поставщиками и подрядчиками")
    expect(d.locator("[name=credit_account] option:checked")).to_have_text("51 Расчётные счета")
    d.locator("[name=amount]").fill("1 500,50")
    d.locator("[aria-label='Контрагент (дебет)']").select_option(label="Поставщик Плюс (7701111111)")
    d.locator("[aria-label='Договор (дебет)']").select_option(index=1)
    d.locator("[aria-label='Расчётный счёт (кредит)']").select_option(index=1)
    expect(d.locator("[aria-label='Статья ДДС (кредит)'] option:checked")).to_have_text("Оплата поставщикам")
    d.locator("[name=description]").fill("Оплата по счёту 15")
    page.screenshot(path=f"{OUT}/10_operation_form.png", full_page=True)
    d.locator("button", has_text="Сохранить и провести").click()
    row = page.locator(".tabulator-row", has_text="Оплата по счёту 15")
    expect(row).to_contain_text("Проведена")
    expect(row).to_contain_text("1 500,50")
    expect(row).to_contain_text("60.01 → 51")

    page.click("text=Новая операция")
    d = page.locator("dialog[open]")
    d.locator("[name=debit_account]").select_option(label="60.01 Расчёты с поставщиками и подрядчиками")
    d.locator("[name=description]").fill("Неполный черновик")
    d.locator("button", has_text="Сохранить черновик").click()
    expect(page.locator(".tabulator-row", has_text="Неполный черновик")).to_contain_text("Черновик")

    page.locator(".tabulator-row", has_text="Неполный черновик").locator("input[type=checkbox]").check()
    page.locator(".bulk-bar button", has_text="Провести").click()
    bulk_dialog = page.locator("dialog[open]")
    expect(bulk_dialog).to_contain_text("выполнено 0, не выполнено 1")
    expect(bulk_dialog).to_contain_text("не указана сумма")
    page.screenshot(path=f"{OUT}/11_bulk_errors.png", full_page=True)
    bulk_dialog.locator("button", has_text="Понятно").click()

    page.locator(".tabulator-row", has_text="Оплата по счёту 15").locator(".tabulator-cell[tabulator-field=description]").click()
    d = page.locator("dialog[open]")
    expect(d.locator("h2")).to_contain_text("Проведена")
    expect(d.locator("[name=amount]")).to_be_disabled()
    d.locator("button", has_text="История").click()
    expect(d.locator(".history")).to_contain_text("провёл(а)")
    d.locator("button", has_text="Закрыть").click()
    expect(page.locator(".bulk-bar")).to_be_hidden()  # открытие операции не отмечает строку
    page.screenshot(path=f"{OUT}/12_journal.png", full_page=True)

    # начальные остатки: дата, расхождение, затем сходится
    page.click("#nav a[data-page=opening]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.locator("[aria-label='Дата начальных остатков']").fill("2026-09-30")
    page.click("text=Сохранить дату")
    expect(page.locator(".check-box")).to_contain_text("ещё не введены")
    page.click("text=Добавить остатки")
    d = page.locator("dialog[open]")
    expect(d.locator("[name=op_date]")).to_have_value("2026-09-30")
    d.locator("[name=debit_account]").select_option(label="51 Расчётные счета")
    d.locator("[name=credit_account]").select_option(label="00 Ввод остатков")
    d.locator("[name=amount]").fill("10000")
    d.locator("[aria-label='Расчётный счёт (дебет)']").select_option(index=1)
    d.locator("[aria-label='Статья ДДС (дебет)']").select_option(label="Прочие поступления")
    d.locator("button", has_text="Сохранить и провести").click()
    expect(page.locator(".check-box")).to_contain_text("Расхождение 10 000,00 ₽: активов введено больше")
    page.click("text=Добавить остатки")
    d = page.locator("dialog[open]")
    d.locator("[name=debit_account]").select_option(label="00 Ввод остатков")
    d.locator("[name=credit_account]").select_option(label="80 Уставный капитал")
    d.locator("[name=amount]").fill("10000")
    d.locator("button", has_text="Сохранить и провести").click()
    expect(page.locator(".check-box")).to_contain_text("сходятся")
    expect(page.locator("[aria-label='Дата начальных остатков']")).to_be_disabled()
    page.screenshot(path=f"{OUT}/13_opening.png", full_page=True)

    # деньги: остаток на начало из начальных остатков, движение, быстрое поступление
    page.click("#nav a[data-page=money]")
    row = page.locator(".tabulator-row", has_text="Сбер, основной")
    expect(row).to_contain_text("10 000,00")   # на начало
    expect(row).to_contain_text("1 500,50")    # списано по журналу
    expect(row).to_contain_text("8 499,50")    # на конец
    row.locator(".tabulator-cell").first.click()
    expect(page.locator("h2", has_text="Движение: Сбер, основной")).to_contain_text("на начало 10 000,00")
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_contain_text("8 499,50")
    page.click("button:has-text('Поступление')")
    d = page.locator("dialog[open]")
    expect(d.locator("[name=debit_account] option:checked")).to_have_text("51 Расчётные счета")
    expect(d.locator("[aria-label='Расчётный счёт (дебет)'] option:checked")).to_contain_text("Сбер, основной")
    expect(d.locator("[name=credit_account] option", has_text="00 Ввод остатков")).to_have_count(0)  # 00 только для остатков
    page.screenshot(path=f"{OUT}/14_money.png", full_page=True)
    d.locator("button", has_text="Отмена").click()

    # касса ЦФО: добавить (видна без операций), кнопки кассы, убрать пустую
    page.click("button:has-text('Добавить кассу')")
    d = page.locator("dialog[open]")
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("button[type=submit]").click()
    cash = page.locator(".tabulator-row", has_text="Касса — Филиал Юг")
    expect(cash).to_be_visible()
    cash.locator(".tabulator-cell").first.click()
    expect(page.locator("button", has_text="Сдать в банк")).to_be_visible()
    page.click("button:has-text('Выдать под отчёт')")
    d = page.locator("dialog[open]")
    expect(d.locator("[name=debit_account] option:checked")).to_contain_text("71 ")
    expect(d.locator("[aria-label='ЦФО (кредит)'] option:checked")).to_contain_text("Филиал Юг")
    d.locator("button", has_text="Отмена").click()
    page.click("button:has-text('Убрать кассу')")
    expect(page.locator(".tabulator-row", has_text="Касса — Филиал Юг")).to_have_count(0)

    # загрузка выписки: два платежа, повторная загрузка не создаёт дублей
    stmt = "\n".join([
        "1CClientBankExchange", "ВерсияФормата=1.03", "ДатаНачала=01.10.2026", "ДатаКонца=31.10.2026",
        "РасчСчет=40702810900000012345", "СекцияРасчСчет", "ДатаНачала=01.10.2026", "ДатаКонца=31.10.2026",
        "НачальныйОстаток=10000", "РасчСчет=40702810900000012345", "ВсегоСписано=300", "ВсегоПоступило=5000",
        "КонечныйОстаток=14700", "КонецРасчСчет",
        "СекцияДокумент=Платежное поручение", "Номер=7", "Дата=10.10.2026", "Сумма=5000",
        "ПлательщикСчет=40702810000000000077", "Плательщик=ООО Покупатель", "ПлательщикИНН=7709999999",
        "ПолучательСчет=40702810900000012345", "ДатаПоступило=10.10.2026", "Получатель=ООО Ромашка",
        "ПолучательИНН=7701234567", "НазначениеПлатежа=Оплата по счёту 7", "КонецДокумента",
        "СекцияДокумент=Банковский ордер", "Номер=8", "Дата=11.10.2026", "Сумма=300",
        "ПлательщикСчет=40702810900000012345", "ДатаСписано=11.10.2026", "Плательщик=ООО Ромашка",
        "ПлательщикИНН=7701234567", "ПолучательСчет=47423810000000000099", "Получатель=Банк",
        "ПолучательИНН=7707083893", "ВидОплаты=17", "НазначениеПлатежа=Комиссия за обслуживание", "КонецДокумента",
        "КонецФайла",
    ])
    stmt_path = f"{OUT}/kl_to_1c.txt"
    with open(stmt_path, "wb") as fh:
        fh.write(stmt.encode("cp1251"))
    page.locator("input[type=file]").set_input_files(stmt_path)
    d = page.locator("dialog[open]", has_text="Выписка загружена")
    expect(d).to_contain_text("Создано черновиков: 2")
    expect(d).to_contain_text("Новые контрагенты (1): ООО Покупатель")
    expect(d).to_contain_text("Все платежи файла учтены")
    page.screenshot(path=f"{OUT}/15_statement.png", full_page=True)
    d.locator("button", has_text="Понятно").click()
    page.locator("input[type=file]").set_input_files(stmt_path)
    d = page.locator("dialog[open]", has_text="Выписка загружена")
    expect(d).to_contain_text("Создано черновиков: 0")
    expect(d).to_contain_text("уже были загружены раньше: 2")
    d.locator("button", has_text="Понятно").click()

    # загрузка из Excel: шаблон скачивается, контрагенты и операции загружаются, ошибки строк видны
    from openpyxl import Workbook
    page.click("#nav a[data-page=import]")
    with page.expect_download() as dl:
        page.locator(".import-card", has_text="Операции").locator("button", has_text="Скачать шаблон").click()
    assert dl.value.suggested_filename == "operations.xlsx"
    def xlsx(path, rows):
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        wb.save(path)
    cp_path = f"{OUT}/counterparties.xlsx"
    xlsx(cp_path, [["Наименование*", "ИНН", "Поставщик"], ["ООО Новый", "7708888888", "да"], ["Плохой", "123", ""]])
    page.locator("input[aria-label='Файл: Контрагенты']").set_input_files(cp_path)
    d = page.locator("dialog[open]", has_text="Файл загружен")
    expect(d).to_contain_text("Добавлено контрагентов: 1 — ООО Новый")
    expect(d).to_contain_text("Строка 3:")
    d.locator("button", has_text="Понятно").click()
    op_path = f"{OUT}/operations.xlsx"
    xlsx(op_path, [["Дата*", "Счёт Дт*", "Счёт Кт*", "Сумма*", "Контрагент (ИНН или наименование)", "Описание"],
                   ["20.10.2026", "44", "60.01", 990, "7708888888", "Реклама из Excel"],
                   ["20.10.2026", "99.99", "60.01", 1, "", ""]])
    page.locator("input[aria-label='Файл: Операции']").set_input_files(op_path)
    d = page.locator("dialog[open]", has_text="Файл загружен")
    expect(d).to_contain_text("Создано черновиков: 1")
    expect(d).to_contain_text("неполных")
    expect(d).to_contain_text("Строка 3: «Счёт Дт»: счёта 99.99 нет в плане счетов")
    page.screenshot(path=f"{OUT}/16_excel.png", full_page=True)
    d.locator("button", has_text="Открыть журнал").click()
    expect(page.locator(".tabulator-row", has_text="Реклама из Excel")).to_be_visible()

    # отчёты: ОСВ, расшифровка цифры, разрез по аналитике, выгрузка в Excel
    page.click("#nav a[data-page=reports]")
    row51 = page.locator(".tabulator-row", has=page.locator(".tabulator-cell[tabulator-field=code]", has_text="51")).first
    expect(row51).to_contain_text("10 000,00")
    expect(row51).to_contain_text("8 499,50")
    expect(page.locator(".tabulator-row.row-total")).to_be_visible()
    row51.locator(".tabulator-cell[tabulator-field=credit] .drill").click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("кредит: 1 500,50")
    expect(d.locator(".tabulator-row", has_text="Сбер, основной")).to_be_visible()
    page.screenshot(path=f"{OUT}/17_drill.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    row51.locator(".link-like").click()
    expect(page.locator("h2", has_text="Счёт 51")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="Сбер, основной")).to_contain_text("8 499,50")
    page.screenshot(path=f"{OUT}/18_osv.png", full_page=True)
    with page.expect_download() as dl:
        page.click("button:has-text('Скачать в Excel')")
    assert dl.value.suggested_filename.startswith("osv_")

    # ДДС: остатки и статья, расшифровка ячейки
    page.click("button.tab:has-text('ДДС')")
    expect(page.locator(".tabulator-row", has_text="Остаток денег на начало")).to_contain_text("10 000,00")
    expect(page.locator(".tabulator-row", has_text="Остаток денег на конец")).to_contain_text("8 499,50")
    supplier_row = page.locator(".tabulator-row", has_text="Оплата поставщикам")
    expect(supplier_row).to_contain_text("-1 500,50")
    supplier_row.locator(".drill").first.click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("кредит: 1 500,50")
    page.screenshot(path=f"{OUT}/19_dds.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    # ОПиУ: в стенде расходов с «Статьёй ОПиУ» нет — отчёт строится, итоговые строки на месте
    page.click("button.tab:has-text('ОПиУ')")
    expect(page.locator(".tabulator-row", has_text="Чистая прибыль")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="Валовая прибыль")).to_be_visible()
    # Баланс: актив = пассив, деньги и уставный капитал; дебиторка раскрывается по контрагентам
    page.click("button.tab:has-text('Баланс')")
    expect(page.locator(".tabulator-row", has_text="Денежные средства")).to_contain_text("8 499,50")
    assets = page.locator(".tabulator-row", has_text="Итого актив").inner_text().split()[-2:]
    passive = page.locator(".tabulator-row", has_text="Итого пассив").inner_text().split()[-2:]
    assert assets == passive, (assets, passive)
    page.locator(".tabulator-row", has_text="Авансы, выданные поставщикам").locator(".drill").last.click()
    d = page.locator("dialog[open]", has_text="Авансы, выданные поставщикам")
    expect(d.locator(".tabulator-row", has_text="Поставщик Плюс")).to_contain_text("1 500,50")
    d.locator(".tabulator-row", has_text="Поставщик Плюс").locator(".drill").click()
    d2 = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d2).to_contain_text("Дебет: 1 500,50")
    page.screenshot(path=f"{OUT}/22_balance.png", full_page=True)
    d2.locator("button", has_text="Закрыть").click()
    d.locator("button", has_text="Закрыть").click()
    # Дебиторка и кредиторка: аванс поставщику, раскрытие до договора и проводок
    page.click("button.tab:has-text('Дебиторка и кредиторка')")
    row = page.locator(".tabulator-row", has_text="Поставщик Плюс").first
    expect(row).to_contain_text("1 500,50")
    row.locator(".tabulator-data-tree-control").click()
    child = page.locator(".tabulator-row", has_text="60.01")
    child.locator(".drill").first.click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("Дебет: 1 500,50")
    page.screenshot(path=f"{OUT}/24_debts.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    page.click("button.tab:has-text('ОСВ')")

    # бюджеты: годовой бюджет, утверждение, пересмотр новой версией
    # ЦФУ (исполнители): отдел → исполнитель
    page.click("#nav a[data-page=references]")
    page.click("button.tab:has-text('ЦФУ')")
    page.click("text=Добавить отдел")
    dialog_fill(page, {"name": "Инженерный отдел"}).locator("button[type=submit]").click()
    page.locator(".tabulator-row", has_text="Инженерный отдел").locator(".tabulator-cell[tabulator-field=_add]").click()
    dialog_fill(page, {"name": "Инженер Петров"}).locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Инженер Петров")).to_be_visible()  # дерево раскрыто

    # инвестпроект
    page.click("button.tab:has-text('Инвестпроекты')")
    page.click("text=Добавить инвестпроект")
    d = dialog_fill(page, {"name": "Новый магазин", "limit_amount": "500 000", "payment_lag_months": "1"})
    d.locator("[name=cfo_id]").select_option(index=1)
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Новый магазин")).to_contain_text("500 000,00")

    page.click("#nav a[data-page=budgets]")
    page.click("text=Новый бюджет")
    d = page.locator("dialog[open]")
    d.locator("[name=year]").fill("2027")
    d.locator("[name=period]").select_option("year:1")
    d.locator("button[type=submit]").click()
    page.locator("select[aria-label='Год']").select_option("2027")
    card = page.locator(".budget-card", has_text="Бюджет 2027")
    expect(card).to_contain_text("Версия 1")
    page.once("dialog", lambda dlg: dlg.accept())
    card.locator("button", has_text="Утвердить").click()
    expect(card.locator("tr", has_text="Версия 1")).to_contain_text("Утверждена")
    card.locator("button", has_text="Новая версия").click()
    d = page.locator("dialog[open]")
    d.locator("[name=name]").fill("Пересмотр за январь")
    d.locator("button[type=submit]").click()
    expect(card.locator("tr", has_text="Пересмотр за январь")).to_contain_text("Черновик")
    page.once("dialog", lambda dlg: dlg.accept())
    card.locator("tr", has_text="Пересмотр за январь").locator("button", has_text="Утвердить").click()
    expect(card.locator("tr", has_text="Версия 1")).to_contain_text("Архив")
    page.screenshot(path=f"{OUT}/27_budgets.png", full_page=True)

    # сетка плана: черновик третьей версии, ввод суммы в ячейку, итоги
    card.locator("button", has_text="Новая версия").click()
    d = page.locator("dialog[open]")
    d.locator("[name=name]").fill("Пересмотр за февраль")
    d.locator("button[type=submit]").click()
    card.locator("tr", has_text="Пересмотр за февраль").locator("button", has_text="Ввести план").click()
    expect(page.locator("h1", has_text="Бюджет 2027 — Пересмотр за февраль")).to_be_visible()
    # у ЦФО с подчинёнными сначала свод по ветке (только просмотр); ввод — в «Свой план»
    expect(page.locator("button.tab.active", has_text="Свод по ветке")).to_be_visible()
    page.click("button.tab:has-text('Свой план')")
    rent = page.locator(".tabulator-row", has_text="Аренда")
    rent.locator(".tabulator-cell[tabulator-field='2027-01']").click()
    page.keyboard.type("1 500,50")
    page.keyboard.press("Enter")
    expect(rent.locator(".tabulator-cell[tabulator-field=total]")).to_have_text("1 500,50")
    expect(page.locator(".toast-error")).to_have_count(0)
    # строка статьи с ЦФУ
    utilities = page.locator(".tabulator-row", has_text="Коммунальные услуги").first
    utilities.locator(".tabulator-cell[tabulator-field=_executor]").click()
    d = page.locator("dialog[open]")
    d.locator("[name=executor_id]").select_option(label="Инженерный отдел / Инженер Петров")
    d.locator("button[type=submit]").click()
    engineer = page.locator(".tabulator-row", has_text="Инженер Петров")
    engineer.locator(".tabulator-cell[tabulator-field='2027-01']").click()
    page.keyboard.type("700")
    page.keyboard.press("Enter")
    expect(engineer.locator(".tabulator-cell[tabulator-field=total]")).to_have_text("700,00")
    expect(page.locator(".toast-error")).to_have_count(0)
    page.screenshot(path=f"{OUT}/28_plan_grid.png", full_page=True)
    # план инвестиций
    page.click("button.tab:has-text('План инвестиций')")
    equipment = page.locator(".tabulator-row", has_text="Инвестиции: оборудование").first
    equipment.locator(".tabulator-cell[tabulator-field='2027-01']").click()
    page.keyboard.type("100 000")
    page.keyboard.press("Enter")
    expect(equipment.locator(".tabulator-cell[tabulator-field=total]")).to_have_text("100 000,00")
    expect(page.locator(".tabulator-group", has_text="Новый магазин")).to_be_visible()
    page.screenshot(path=f"{OUT}/28c_plan_investments.png", full_page=True)
    page.click("button.tab:has-text('План ОПиУ')")
    page.click("button.tab:has-text('По ЦФУ')")
    page.locator("select[aria-label='ЦФУ плана']").select_option(label="Инженерный отдел")
    expect(page.locator(".tabulator-row", has_text="Коммунальные услуги").first).to_contain_text("700,00")
    page.click("button.tab:has-text('Свод по ветке')")
    rent = page.locator(".tabulator-row", has_text="Аренда").first
    expect(rent.locator(".tabulator-cell[tabulator-field=total]")).to_have_text("1 500,50")
    rent.locator(".tabulator-data-tree-control").click()
    expect(page.locator(".tabulator-row", has_text="свой план")).to_contain_text("1 500,50")
    page.screenshot(path=f"{OUT}/28b_plan_branch.png", full_page=True)
    page.click("text=← К бюджетам")
    page.locator("select[aria-label='Год']").select_option("2027")
    expect(page.locator(".budget-card", has_text="Бюджет 2027")).to_be_visible()
    page.once("dialog", lambda dlg: dlg.accept())
    card.locator("tr", has_text="Пересмотр за февраль").locator("button", has_text="Утвердить").click()
    expect(card.locator("tr", has_text="Пересмотр за февраль")).to_contain_text("Утверждена")

    # план/факт за январь 2027: план аренды из утверждённой версии, расшифровка плана
    page.click("#nav a[data-page=reports]")
    page.click("button.tab:has-text('План/факт')")
    page.fill("input[aria-label='С даты']", "2027-01-01")
    page.fill("input[aria-label='По дату']", "2027-01-31")
    page.locator("input[aria-label='По дату']").dispatch_event("change")
    rent = page.locator(".tabulator-row", has_text="Аренда")
    expect(rent).to_contain_text("-1 500,50")
    rent.locator(".tabulator-cell[tabulator-field='2027-01|plan'] .drill").click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("Дебет: 1 500,50")
    page.screenshot(path=f"{OUT}/29_plan_fact.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    page.click("button.tab:has-text('Инвестиции')")
    expect(page.locator(".tabulator-row", has_text="Новый магазин")).to_contain_text("100 000,00")
    page.screenshot(path=f"{OUT}/29b_investments.png", full_page=True)
    page.click("button.tab:has-text('ОСВ')")

    # зарплата: начисление за текущий месяц, премия, проведение в журнал
    page.click("#nav a[data-page=payroll]")
    page.click("text=Новое начисление")
    d = page.locator("dialog[open]")
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="Иванов Иван")
    expect(row).to_contain_text("85 000,00")
    row.locator(".tabulator-cell[tabulator-field=bonus]").click()
    page.keyboard.type("15 000")
    page.keyboard.press("Enter")
    expect(row.locator(".tabulator-cell[tabulator-field=accrued]")).to_have_text("100 000,00")
    expect(row.locator(".tabulator-cell[tabulator-field=ndfl]")).to_have_text("13 000,00")
    expect(page.locator("button", has_text="Скачать для готовых сумм")).to_be_visible()
    # отпускные по среднему (истории нет — оклад / 29,3), затем обнуляем вручную
    row.locator("[data-calc=vacation]").click()
    dialog_fill(page, {"days": "5"}).locator("button[type=submit]").click()
    expect(row.locator(".tabulator-cell[tabulator-field=vacation]")).to_have_text("14 505,10")
    expect(page.locator(".calc-notes")).to_contain_text("Отпуск 5 дн.")
    row.locator(".tabulator-cell[tabulator-field=vacation]").click()
    page.keyboard.type("0")
    page.keyboard.press("Enter")
    expect(row.locator(".tabulator-cell[tabulator-field=accrued]")).to_have_text("100 000,00")
    expect(page.locator(".calc-notes")).to_have_count(0)
    page.click("button:has-text('Провести')")
    expect(page.locator("text=Статус: проведено")).to_be_visible()
    page.screenshot(path=f"{OUT}/31_payroll.png", full_page=True)
    page.click("text=← К списку")
    expect(page.locator("tr", has_text="Ромашка")).to_contain_text("Проведено")

    # ведомость на зарплату: к выплате 87 000, отправка в банк, подтверждение оплаты вручную
    page.click("text=Новая ведомость")
    d = page.locator("dialog[open]")
    d.locator("[name=kind]").select_option("salary")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Иванов Иван")).to_contain_text("87 000,00")
    page.click("button:has-text('Отправить в банк')")
    expect(page.locator("text=Статус: отправлена в банк")).to_be_visible()
    page.click("button:has-text('Подтвердить оплату вручную')")
    page.locator("dialog[open] button[type=submit]").click()
    expect(page.locator("text=(вручную)")).to_be_visible()
    page.screenshot(path=f"{OUT}/32_payout.png", full_page=True)
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("button:has-text('Отменить оплату')")  # остатки денег ниже проверяются без этой выплаты
    expect(page.locator("text=Статус: отправлена в банк")).to_be_visible()
    page.click("text=← К списку")

    # основные средства: карточка, поступление на 08 в журнале, ввод в эксплуатацию, амортизация
    page.click("#nav a[data-page=fixedassets]")
    page.click("text=Новое основное средство")
    d = dialog_fill(page, {"name": "Ноутбук", "inventory_number": "ОС-1", "cost": "120 000", "useful_life_months": "36"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля")
    expect(d.locator("[name=pl_item_id] option:checked")).to_have_text("Амортизация основных средств")
    d.locator("button[type=submit]").click()
    expect(page.locator("tr", has_text="Ноутбук")).to_contain_text("не введено")

    page.click("#nav a[data-page=journal]")
    page.click("text=Новая операция")
    d = page.locator("dialog[open]")
    d.locator("[name=debit_account]").select_option(label="08 Вложения во внеоборотные активы")
    d.locator("[name=credit_account]").select_option(label="60.01 Расчёты с поставщиками и подрядчиками")
    d.locator("[name=amount]").fill("120000")
    d.locator("[aria-label='Основное средство (дебет)']").select_option(label="Ноутбук (ОС-1)")
    d.locator("[aria-label='Контрагент (кредит)']").select_option(label="Поставщик Плюс (7701111111)")
    d.locator("[aria-label='Договор (кредит)']").select_option(index=1)
    d.locator("[name=description]").fill("Покупка ноутбука")
    d.locator("button", has_text="Сохранить и провести").click()
    expect(page.locator(".tabulator-row", has_text="Покупка ноутбука")).to_contain_text("Проведена")

    page.click("#nav a[data-page=fixedassets]")
    row = page.locator("tr", has_text="Ноутбук")
    expect(row).to_contain_text("120 000,00")  # на 08
    row.locator("button", has_text="Ввести в эксплуатацию").click()
    page.locator("dialog[open] button[type=submit]").click()
    expect(page.locator(".toast")).to_have_text("Введено в эксплуатацию")
    expect(row).to_contain_text("3 333,33")  # в месяц
    expect(row.locator("button", has_text="Отменить ввод")).to_be_visible()
    page.locator("select[aria-label='Месяц']").select_option(label="Ноябрь")
    expect(page.locator("tr", has_text="Ноутбук (ОС-1)")).to_contain_text("3 333,33")
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("button:has-text('Начислить за месяц')")
    expect(page.locator("tr", has_text="Ноябрь 2026")).to_contain_text("3 333,33")
    page.screenshot(path=f"{OUT}/33_fixed_assets.png", full_page=True)
    page.once("dialog", lambda dlg: dlg.accept())
    page.locator("tr", has_text="Ноябрь 2026").locator("button", has_text="Отменить").click()
    expect(page.locator("text=Начислений пока нет.")).to_be_visible()
    # списание и его отмена
    page.locator("tr", has_text="Ноутбук").first.locator("button", has_text="Продать / списать").click()
    page.locator("dialog[open] button[type=submit]").click()
    expect(page.locator(".toast")).to_have_text("Списание проведено")
    expect(page.locator("tr", has_text="Ноутбук").first).to_contain_text(f"списано {dt.date.today():%d.%m.%Y}")
    page.once("dialog", lambda dlg: dlg.accept())
    page.locator("tr", has_text="Ноутбук").first.locator("button", has_text="Отменить выбытие").click()
    expect(page.locator("tr", has_text="Ноутбук").first.locator("button", has_text="Продать / списать")).to_be_visible()

    # регламентная операция из операции журнала; закрытие и открытие месяца
    page.click("#nav a[data-page=journal]")
    page.locator(".tabulator-row", has_text="Оплата по счёту 15").locator(".tabulator-cell[tabulator-field=description]").click()
    page.locator("dialog[open] button", has_text="Сделать регламентной").click()
    d = page.locator("dialog[open]").last
    expect(d.locator("[name=name]")).to_have_value("Оплата по счёту 15")
    d.locator("[name=name]").fill("Оплата аренды")
    d.locator("button[type=submit]").click()
    expect(page.locator(".toast")).to_contain_text("Регламентная операция создана")
    page.locator("dialog[open] button", has_text="Закрыть").click()

    page.click("#nav a[data-page=closing]")
    expect(page.locator(".closing-status")).to_have_text("Период не закрыт")
    expect(page.locator("tr", has_text="Оплата аренды")).to_contain_text("сразу")
    page.locator("select[aria-label='Месяц']").select_option(label="Сентябрь")
    expect(page.locator("ul.checklist")).to_contain_text("Нет черновиков")
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("button:has-text('Закрыть месяц')")
    expect(page.locator(".closing-status")).to_have_text("Период закрыт по 30.09.2026")
    # октябрь: черновик мешает закрыть
    expect(page.locator("select[aria-label='Месяц'] option:checked")).to_have_text("Октябрь")
    expect(page.locator("li.check-fail", has_text="Нет черновиков")).to_be_visible()
    expect(page.locator("li.check-fail", has_text="Финансовый результат определён")).to_be_visible()  # зарплата на 44
    page.click("button:has-text('Определить финансовый результат')")
    expect(page.locator("tr", has_text="Закрытие 26, 44, 90, 91 на 99")).to_be_visible()
    expect(page.locator("li.check-ok", has_text="Финансовый результат определён")).to_be_visible()
    page.once("dialog", lambda dlg: dlg.accept())
    page.locator("button.danger", has_text="Отменить").first.click()
    expect(page.locator("text=За этот месяц финансовый результат ещё не определяли.")).to_be_visible()
    expect(page.locator("button:has-text('Закрыть месяц')")).to_be_disabled()
    # ноябрь: регламентная операция ждёт выполнения
    page.locator("select[aria-label='Месяц']").select_option(label="Ноябрь")
    expect(page.locator("tr", has_text="Оплата аренды").first).to_contain_text("не выполнена")
    page.screenshot(path=f"{OUT}/34_closing.png", full_page=True)
    page.once("dialog", lambda dlg: dlg.accept())
    page.click("button:has-text('Открыть сентябрь 2026')")
    expect(page.locator(".closing-status")).to_have_text("Период не закрыт")

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

    # руководитель ЦФО: роль с ЦФО «Торговля»
    page.click("text=Добавить пользователя")
    d = dialog_fill(page, {"login": "head", "full_name": "Петров П.П.", "password": "head-password-1"})
    d.locator("select[aria-label='Роль в Группа АВМВ']").select_option("cfo_head")
    cfo_select = d.locator("select[aria-label='ЦФО в Группа АВМВ']")
    expect(cfo_select).to_be_visible()
    cfo_select.select_option(label="АВМВ / Торговля")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Руководитель ЦФО (Торговля)")).to_be_visible()

    # вход под CEO
    page.click("#logout")
    login(page, "ceo", "ceo-password-1")
    expect(page.locator("#nav a")).to_have_count(16)  # нет «Пользователи»
    page.click("#nav a[data-page=organizations]")
    expect(page.locator("text=Добавить организацию")).to_have_count(0)
    expect(page.locator(".tabulator").first.locator(".tabulator-row")).to_have_count(1)  # видит только свою
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()  # выбрана автоматически
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator("text=Добавить юрлицо")).to_be_hidden()
    page.goto(BASE + "/#/users")  # прямой переход в чужой раздел
    expect(page.locator("#nav a.active")).to_have_text("Журнал операций")
    page.click("#nav a[data-page=organizations]")
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]", has_text="✎")).to_have_count(0)
    page.screenshot(path=f"{OUT}/4_ceo.png", full_page=True)

    # CEO видит журнал, но не вводит и не проводит
    page.click("#nav a[data-page=journal]")
    expect(page.locator(".tabulator-row", has_text="Оплата по счёту 15")).to_be_visible()
    expect(page.locator("text=Новая операция")).to_be_hidden()
    page.locator(".tabulator-row", has_text="Оплата по счёту 15").locator("input[type=checkbox]").check()
    expect(page.locator(".bulk-bar")).to_be_hidden()

    # CEO видит отчёты
    page.click("#nav a[data-page=reports]")
    expect(page.locator(".tabulator-row", has_text="Расчётные счета")).to_contain_text("8 499,50")
    page.click("button.tab:has-text('Склад')")
    expect(page.locator(".tabulator-col-title", has_text="Остаток на конец")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="Итого")).to_be_visible()
    page.click("button.tab:has-text('ОСВ')")

    # CEO видит деньги без кнопок ввода
    page.click("#nav a[data-page=money]")
    expect(page.locator(".tabulator-row", has_text="Сбер, основной")).to_contain_text("8 499,50")
    expect(page.locator("button:has-text('Поступление')")).to_have_count(0)

    # CEO видит основные средства без кнопок
    page.click("#nav a[data-page=fixedassets]")
    expect(page.locator("tr", has_text="Ноутбук")).to_contain_text("120 000,00")
    expect(page.locator("text=Новое основное средство")).to_be_hidden()
    expect(page.locator("button:has-text('Отменить ввод')")).to_have_count(0)
    expect(page.locator("h2", has_text="Амортизация")).to_have_count(0)

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

    # руководитель ЦФО: журнал и отчёты только по своей ветке, без выписок и начальных остатков
    page.click("#logout")
    login(page, "head", "head-password-1")
    page.click("#nav a[data-page=journal]")
    expect(page.locator(".tabulator-row", has_text="Оплата по счёту 15")).to_have_count(0)
    page.click("#nav a[data-page=money]")
    expect(page.locator("h1", has_text="Деньги")).to_be_visible()
    expect(page.locator("button:has-text('Загрузить выписку')")).to_have_count(0)
    expect(page.locator(".tabulator-row", has_text="Сбер, основной")).to_have_count(0)
    page.click("#nav a[data-page=opening]")
    expect(page.locator("text=Раздел недоступен руководителю ЦФО")).to_be_visible()
    page.click("#nav a[data-page=reports]")
    expect(page.locator("select[aria-label='ЦФО'] option").first).to_have_text("Вся моя ветка")
    expect(page.locator(".tabulator-row", has_text="Расчётные счета")).to_have_count(0)
    page.screenshot(path=f"{OUT}/26_cfo_head.png", full_page=True)
    browser.close()

print("ОШИБКИ В КОНСОЛИ:", errors or "нет")
print("UI-ПРОВЕРКА ПРОЙДЕНА")
