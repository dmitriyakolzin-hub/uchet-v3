"""ЦФО (дерево из 5 уровней) и сотрудники — справочники организации (docs/TZ.md, раздел 7)."""

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import Ltree
from app.models.reference import OrgScoped

CFO_LEVELS = {
    1: "Компания",
    2: "Направление",
    3: "Филиал",
    4: "Магазин",
    5: "Подразделение учёта",
}


class Cfo(OrgScoped, Base):
    """Узел дерева ЦФО. Уровень потомка = уровень родителя + 1; у «Компании» родителя нет."""

    __tablename__ = "cfos"
    __table_args__ = (
        CheckConstraint("level BETWEEN 1 AND 5", name="level"),
        CheckConstraint("(level = 1) = (parent_id IS NULL)", name="root_has_no_parent"),
        CheckConstraint("level = 1 OR legal_entity_id IS NULL", name="legal_entity_only_for_company"),
        CheckConstraint("nlevel(path) = level", name="path_depth"),
        Index("ix_cfos_path", "path", postgresql_using="gist"),
    )

    name: Mapped[str] = mapped_column(String(255))
    level: Mapped[int] = mapped_column(SmallInteger)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("cfos.id", ondelete="RESTRICT"), index=True
    )
    # Путь от корня по id: «12.34.56». Нужен для отбора ветки (этап 3: доступ по веткам ЦФО)
    path: Mapped[str] = mapped_column(Ltree)
    legal_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="RESTRICT")
    )


class Employee(OrgScoped, Base):
    """Сотрудник организации. Расчёт зарплаты — этап 4."""

    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("inn IS NULL OR inn ~ '^[0-9]{12}$'", name="inn"),
        CheckConstraint("fired_on IS NULL OR hired_on IS NULL OR fired_on >= hired_on", name="dates"),
        Index(
            "uq_employees_organization_personnel_number",
            "organization_id",
            "personnel_number",
            unique=True,
            postgresql_where=text("personnel_number <> ''"),
        ),
    )

    full_name: Mapped[str] = mapped_column(String(255))
    personnel_number: Mapped[str] = mapped_column(String(50), default="", server_default="")
    inn: Mapped[str | None] = mapped_column(String(12))
    position: Mapped[str] = mapped_column(String(255), default="", server_default="")
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"), index=True)
    hired_on: Mapped[date | None] = mapped_column(Date)
    fired_on: Mapped[date | None] = mapped_column(Date)
