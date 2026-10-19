"""Виды аналитики, которую можно сделать обязательной на счёте (docs/TZ.md, раздел 7).

Сами справочники (контрагенты, ЦФО и т.д.) появляются в частях 1.3б–1.4; здесь — только их коды.
Порядок в перечислении — порядок колонок аналитики в проводке.
"""

from enum import StrEnum


class AnalyticKind(StrEnum):
    BANK_ACCOUNT = "bank_account"
    COUNTERPARTY = "counterparty"
    CONTRACT = "contract"
    EMPLOYEE = "employee"
    NOMENCLATURE = "nomenclature"
    WAREHOUSE = "warehouse"
    FIXED_ASSET = "fixed_asset"
    CFO = "cfo"
    EXECUTOR = "executor"
    CASH_FLOW_ITEM = "cash_flow_item"
    PL_ITEM = "pl_item"


ANALYTIC_TITLES: dict[AnalyticKind, str] = {
    AnalyticKind.BANK_ACCOUNT: "Расчётный счёт",
    AnalyticKind.COUNTERPARTY: "Контрагент",
    AnalyticKind.CONTRACT: "Договор",
    AnalyticKind.EMPLOYEE: "Сотрудник",
    AnalyticKind.NOMENCLATURE: "Номенклатура",
    AnalyticKind.WAREHOUSE: "Склад",
    AnalyticKind.FIXED_ASSET: "Основное средство",
    AnalyticKind.CFO: "ЦФО",
    AnalyticKind.EXECUTOR: "ЦФУ",
    AnalyticKind.CASH_FLOW_ITEM: "Статья ДДС",
    AnalyticKind.PL_ITEM: "Статья ОПиУ",
}

# Необязательная аналитика: вид → при каком обязательном виде её можно указать (ЦФУ — там, где ведётся ЦФО)
OPTIONAL_ANALYTICS: dict[str, str] = {AnalyticKind.EXECUTOR: AnalyticKind.CFO}

ACCOUNT_TYPES = {
    "active": "Активный",
    "passive": "Пассивный",
    "active_passive": "Активно-пассивный",
}
