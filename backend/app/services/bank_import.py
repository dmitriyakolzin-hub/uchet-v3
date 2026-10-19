"""Загрузка банковской выписки в журнал черновиками (docs/TZ.md, раздел 11).

Каждый платёж → черновик операции через journal.save_draft (те же проверки, что при ручном вводе).
Повторная загрузка не создаёт дублей (import_key), переводы между своими счетами не задваиваются.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Article,
    BankAccount,
    Contract,
    Counterparty,
    Currency,
    Operation,
    Organization,
    Posting,
    User,
)
from app.models.journal import analytic_column
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal
from app.services.bank_statement import Document, Statement, parse
from app.services.journal import JournalError
from app.services.money import bank_balance

# Статьи стартового набора, которые подбираются по правилам
TRANSFER = "Перевод между своими счетами"
COMMISSION = "Банковские комиссии"
TAX = "Уплата прочих налогов"
SALARY = "Выплата заработной платы"
LOAN_IN = "Получение кредитов и займов"
LOAN_OUT = "Погашение кредитов и займов"
DEFAULT_IN = "Поступление от покупателей"
DEFAULT_OUT = "Оплата поставщикам"


@dataclass
class AccountCheck:
    bank_account_id: int
    name: str
    number: str
    statement_opening: Decimal | None
    statement_closing: Decimal | None
    statement_in: Decimal
    statement_out: Decimal
    documents_in: Decimal
    documents_out: Decimal
    system_opening: Decimal
    system_closing: Decimal

    @property
    def totals_match(self) -> bool:
        return self.statement_in == self.documents_in and self.statement_out == self.documents_out


@dataclass
class ImportResult:
    date_from: dt.date | None
    date_to: dt.date | None
    created: list[int] = field(default_factory=list)  # номера созданных черновиков
    incomplete: list[int] = field(default_factory=list)  # номера черновиков, которые нельзя провести сразу
    skipped_duplicates: int = 0
    skipped_before_opening: int = 0
    new_counterparties: list[str] = field(default_factory=list)
    accounts: list[AccountCheck] = field(default_factory=list)


def _valid_inn(inn: str) -> bool:
    return bool(re.fullmatch(r"\d{10}|\d{12}", inn or "")) and set(inn) != {"0"}


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()[:255]


class _Importer:
    def __init__(self, db: Session, organization_id: int, user: User, statement: Statement):
        self.db, self.org_id, self.user, self.st = db, organization_id, user, statement
        self.org = db.get(Organization, organization_id)
        self.result = ImportResult(date_from=statement.date_from, date_to=statement.date_to)
        self.banks = {
            b.number: b for b in db.scalars(select(BankAccount).where(BankAccount.organization_id == organization_id))
        }
        self.articles = {a.name: a for a in db.scalars(select(Article).where(Article.is_active))}
        self.accounts = {a.code: a for a in db.scalars(select(Account))}
        self.accounts_by_id = {a.id: a for a in self.accounts.values()}
        self.currencies = {c.id: c for c in db.scalars(select(Currency))}
        self.counterparties = {
            c.inn: c for c in db.scalars(
                select(Counterparty).where(Counterparty.organization_id == organization_id, Counterparty.inn.is_not(None))
            )
        }

    # --- подбор ---

    def money_account(self, bank: BankAccount) -> Account:
        return self.accounts["51" if self.currencies[bank.currency_id].code == "RUB" else "52"]

    def article_for(self, doc: Document, inflow: bool, counterparty: Counterparty | None) -> Article | None:
        purpose = doc.purpose.lower()
        rules = [
            (not inflow and (doc.payment_kind == "17" or "комисси" in purpose), COMMISSION),
            (not inflow and doc.kbk.strip() not in ("", "0"), TAX),
            (not inflow and "заработн" in purpose, SALARY),
            ("кредит" in purpose or "займ" in purpose, LOAN_IN if inflow else LOAN_OUT),
        ]
        for matched, name in rules:
            if matched and name in self.articles:
                return self.articles[name]
        if counterparty is not None and counterparty.id is not None:
            previous = self.previous_article(counterparty, "inflow" if inflow else "outflow")
            if previous is not None:
                return previous
        return self.articles.get(DEFAULT_IN if inflow else DEFAULT_OUT)

    def previous_article(self, counterparty: Counterparty, direction: str) -> Article | None:
        """Статья последней операции с этим контрагентом в том же направлении."""
        posting_has_cp = (Posting.dt_counterparty_id == counterparty.id) | (Posting.ct_counterparty_id == counterparty.id)
        return self.db.scalar(
            select(Article)
            .join(Operation, Operation.article_id == Article.id)
            .join(Posting, Posting.operation_id == Operation.id)
            .where(Operation.organization_id == self.org_id, posting_has_cp, Operation.status != "cancelled",
                   Article.is_cash_flow, Article.cash_flow_direction == direction, ~Article.is_transfer)
            .order_by(Operation.op_date.desc(), Operation.id.desc())
            .limit(1)
        )

    def contract_for(self, counterparty: Counterparty, legal_entity_id: int) -> int | None:
        """Договор последней операции с контрагентом, иначе — единственный действующий договор."""
        for side in ("debit", "credit"):
            cp_col = getattr(Posting, analytic_column(side, "counterparty"))
            contract_col = getattr(Posting, analytic_column(side, "contract"))
            found = self.db.scalar(
                select(contract_col)
                .join(Operation, Operation.id == Posting.operation_id)
                .where(Operation.organization_id == self.org_id, Operation.legal_entity_id == legal_entity_id,
                       cp_col == counterparty.id, contract_col.is_not(None))
                .order_by(Operation.op_date.desc(), Operation.id.desc())
                .limit(1)
            )
            if found:
                return found
        contracts = list(self.db.scalars(select(Contract.id).where(
            Contract.counterparty_id == counterparty.id, Contract.legal_entity_id == legal_entity_id, Contract.is_active)))
        return contracts[0] if len(contracts) == 1 else None

    def counterparty_for(self, inn: str, name: str, inflow: bool, create: bool) -> Counterparty | None:
        if not _valid_inn(inn):
            return None
        cp = self.counterparties.get(inn)
        if cp is None and create:
            cp = Counterparty(
                organization_id=self.org_id, name=_clean_name(name) or inn, full_name=_clean_name(name)[:500],
                kind="legal" if len(inn) == 10 else "entrepreneur", inn=inn,
                is_customer=inflow, is_supplier=not inflow,
            )
            self.db.add(cp)
            self.db.flush()
            self.counterparties[inn] = cp
            self.result.new_counterparties.append(cp.name)
        return cp

    # --- платёж → черновик ---

    def import_document(self, doc: Document) -> None:
        payer, payee = self.banks.get(doc.payer_account), self.banks.get(doc.payee_account)
        if payer is None and payee is None:
            return  # платёж не по нашим счетам
        transfer = payer is not None and payee is not None and payer.legal_entity_id == payee.legal_entity_id
        inflow = payee is not None and not transfer
        own = payee if inflow else payer
        op_date = (doc.credited_on if inflow else doc.debited_on) or doc.doc_date
        if transfer:
            op_date = doc.debited_on or doc.credited_on or doc.doc_date
        if self.org.opening_balance_date and op_date <= self.org.opening_balance_date:
            self.result.skipped_before_opening += 1
            return

        if transfer:
            low, high = sorted((payer.id, payee.id))
            key = f"tr:{doc.doc_date}:{doc.number}:{doc.amount}:{low}:{high}"
        else:
            other = doc.payer_account if inflow else doc.payee_account
            other_inn = doc.payer_inn if inflow else doc.payee_inn
            key = f"{own.id}:{'in' if inflow else 'out'}:{doc.doc_date}:{doc.number}:{doc.amount}:{other or other_inn}"
        if self.db.scalar(select(Operation.id).where(Operation.organization_id == self.org_id, Operation.import_key == key)):
            self.result.skipped_duplicates += 1
            return

        posting, article = self.build_posting(doc, transfer, inflow, own, payer, payee)
        other_name = _clean_name(doc.payer_name if inflow else doc.payee_name)
        body = OperationIn(
            legal_entity_id=own.legal_entity_id, op_date=op_date, article_id=article.id if article else None,
            description=f"{doc.purpose} ({doc.kind.lower()} № {doc.number} от {doc.doc_date:%d.%m.%Y}"
                        f"{', ' + other_name if other_name and not transfer else ''})"[:2000],
            postings=[posting],
        )
        op = Operation(organization_id=self.org_id, created_by=self.user.id, status="draft", import_key=key)
        journal.save_draft(self.db, op, body, self.user)
        op.number = journal.next_number(self.db, self.org_id)
        self.db.add(op)
        self.db.flush()
        journal._event(self.db, op, self.user, "created")
        self.result.created.append(op.number)
        if journal.posting_errors(self.db, op):
            self.result.incomplete.append(op.number)

    def build_posting(self, doc, transfer, inflow, own, payer, payee) -> tuple[PostingIn, Article | None]:
        if transfer:
            article = self.articles.get(TRANSFER)
            item = {"cash_flow_item": article.id} if article else {}
            return PostingIn(
                debit_account_id=self.money_account(payee).id, credit_account_id=self.money_account(payer).id,
                amount=doc.amount, currency_id=payer.currency_id,
                debit=AnalyticsIn(bank_account=payee.id, **item), credit=AnalyticsIn(bank_account=own.id, **item),
            ), article

        other_inn, other_name = (doc.payer_inn, doc.payer_name) if inflow else (doc.payee_inn, doc.payee_name)
        known_cp = self.counterparty_for(other_inn, other_name, inflow, create=False)
        article = self.article_for(doc, inflow, known_cp)
        template = None
        if article is not None:
            template = article.credit_account_id if inflow else article.debit_account_id
        other_account = self.accounts_by_id.get(template) if template else None

        money = AnalyticsIn(bank_account=own.id, cash_flow_item=article.id if article and article.is_cash_flow else None)
        other = AnalyticsIn()
        if other_account is not None:
            required = {a.kind for a in other_account.analytics}
            if "counterparty" in required:
                cp = self.counterparty_for(other_inn, other_name, inflow, create=True)
                if cp is not None:
                    other.counterparty = cp.id
                    if "contract" in required:
                        other.contract = self.contract_for(cp, own.legal_entity_id)
            if "cash_flow_item" in required and article.is_cash_flow:
                other.cash_flow_item = article.id
            if "pl_item" in required and article.is_pl:
                other.pl_item = article.id
        money_account = self.money_account(own).id
        other_id = other_account.id if other_account else None
        return PostingIn(
            debit_account_id=money_account if inflow else other_id,
            credit_account_id=other_id if inflow else money_account,
            amount=doc.amount, currency_id=own.currency_id,
            debit=money if inflow else other, credit=other if inflow else money,
        ), article

    # --- сверка ---

    def check(self, number: str) -> AccountCheck:
        bank = self.banks[number]
        opening, closing, total_in, total_out = self.st.balances(number)
        docs_in = sum((d.amount for d in self.st.documents if d.payee_account == number), Decimal(0))
        docs_out = sum((d.amount for d in self.st.documents if d.payer_account == number), Decimal(0))
        day_before = self.st.date_from - dt.timedelta(days=1) if self.st.date_from else None
        return AccountCheck(
            bank_account_id=bank.id, name=bank.name, number=bank.number,
            statement_opening=opening, statement_closing=closing, statement_in=total_in, statement_out=total_out,
            documents_in=docs_in, documents_out=docs_out,
            system_opening=bank_balance(self.db, self.org_id, bank.id, day_before) if day_before else Decimal("0.00"),
            system_closing=bank_balance(self.db, self.org_id, bank.id, self.st.date_to) if self.st.date_to else Decimal("0.00"),
        )


def import_statement(db: Session, organization_id: int, user: User, raw: bytes) -> ImportResult:
    statement = parse(raw)
    importer = _Importer(db, organization_id, user, statement)
    numbers = list(dict.fromkeys(statement.accounts + [s.account for s in statement.sections]))
    unknown = [n for n in numbers if n not in importer.banks]
    if unknown:
        raise JournalError(
            "Расчётный счёт " + ", ".join(unknown) + " не заведён в справочнике организации — "
            "добавьте его на вкладке «Справочники → Расчётные счета» и загрузите выписку снова"
        )
    for doc in statement.documents:
        importer.import_document(doc)
    importer.result.accounts = [importer.check(n) for n in numbers]
    return importer.result
