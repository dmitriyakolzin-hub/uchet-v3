"""Примечания к цифрам отчётов и к операциям журнала, метки примечаний (docs/TZ.md, раздел 23)."""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.reference import OrgScoped


class NoteLabel(OrgScoped, Base):
    """Метка примечания: у каждой организации свои, с цветом («Проверить», «Ошибка», …)."""

    __tablename__ = "note_labels"
    __table_args__ = (CheckConstraint("color ~ '^#[0-9a-fA-F]{6}$'", name="color"),)

    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#f79009", server_default="#f79009")


class Note(Base):
    """Примечание к ячейке отчёта (отчёт + вариант + строка + колонка + фильтры) или к операции журнала."""

    __tablename__ = "notes"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="status"),
        CheckConstraint("(operation_id IS NULL) <> (report = 'operation')", name="operation_note"),
        Index("ix_notes_organization_report", "organization_id", "report"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    # отчёт: pl, dds, planfact, management, …; operation — примечание к операции журнала
    report: Mapped[str] = mapped_column(String(30))
    variant: Mapped[str] = mapped_column(String(30), default="", server_default="")  # вид внутри отчёта (план/факт ОПиУ или ДДС)
    row_key: Mapped[str] = mapped_column(String(100), default="", server_default="")
    row_title: Mapped[str] = mapped_column(String(500), default="", server_default="")
    column_key: Mapped[str] = mapped_column(String(50), default="", server_default="")
    column_title: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # фильтры отчёта, при которых поставлено примечание (колонка «Итого» — ещё и период)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id", ondelete="SET NULL"))
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="SET NULL"))
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="SET NULL"))
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="CASCADE"), index=True)
    label_id: Mapped[int | None] = mapped_column(ForeignKey("note_labels.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
