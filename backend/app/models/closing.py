"""Закрытие периода и регламентные операции (docs/TZ.md, раздел 14, «Детали 4.5»)."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.reference import OrgScoped


class PeriodClosing(Base):
    """История закрытия и открытия периода: кто, когда, по какую дату."""

    __tablename__ = "period_closings"
    __table_args__ = (CheckConstraint("action IN ('close', 'reopen')", name="action"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    action: Mapped[str] = mapped_column(String(10))
    closed_through: Mapped[date | None] = mapped_column(Date)  # стало
    previous: Mapped[date | None] = mapped_column(Date)  # было
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoutineRule(OrgScoped, Base):
    """Регламентная операция: повторяется каждый месяц по образцу (шапка без даты + проводки)."""

    __tablename__ = "routine_rules"
    __table_args__ = (
        CheckConstraint("day BETWEEN 1 AND 31", name="day"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="period"),
    )

    name: Mapped[str] = mapped_column(String(255))
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    day: Mapped[int] = mapped_column(SmallInteger)  # число месяца; больше дней в месяце — последний день
    valid_from: Mapped[date] = mapped_column(Date)  # первый месяц (1-е число)
    valid_to: Mapped[date | None] = mapped_column(Date)  # последний месяц (1-е число); None — бессрочно
    post: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # проводить сразу
    # образец: {"article_id", "description", "postings": [PostingIn, …]} — см. app/schemas/journal.py
    template: Mapped[dict] = mapped_column(JSONB)


class RoutineRun(Base):
    """Выполнение правила за месяц; удаление операции в журнале удаляет и отметку о выполнении."""

    __tablename__ = "routine_runs"
    __table_args__ = (
        UniqueConstraint("rule_id", "year", "month", name="uq_routine_runs_rule_month"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("routine_rules.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id", ondelete="CASCADE"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResultClosing(Base):
    """Операция определения финансового результата за месяц (month) или реформации за год (year)."""

    __tablename__ = "result_closings"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "year", "month", "kind", name="uq_result_closings_entity_month_kind"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
        CheckConstraint("kind IN ('month', 'year')", name="kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(String(10))
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id", ondelete="CASCADE"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
