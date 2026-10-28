"""Форма управленческого отчёта организации (docs/TZ.md, раздел 19, «Детали 9.4»): строки загруженного справочника."""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportFormRow(Base):
    """Строка формы: заголовок раздела, статья (сумма по статье с подстатьями) или показатель (формула по кодам)."""

    __tablename__ = "report_form_rows"
    __table_args__ = (CheckConstraint("kind IN ('header', 'article', 'indicator')", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    code: Mapped[str | None] = mapped_column(String(30))  # без точки в конце: «2.6.13.1»
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(10))
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    formula: Mapped[str] = mapped_column(String(500), default="", server_default="")
    invert: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # обратный знак
    note: Mapped[str] = mapped_column(String(100), default="", server_default="")
