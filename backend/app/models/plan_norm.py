"""Нормативы планирования (docs/TZ.md, раздел 31, «Детали 23.1»): задаёт финансовый директор на вкладке «Нормативы»
страницы «Бюджеты».

- «Расчётный» (kind calc): процент от базового показателя — статьи или строки управленческой формы (по коду);
- «Фактический» (kind fact): сумма в месяц вручную — прибавляется к регламентным операциям;
- «Нормативный» (kind norm): сумма в месяц — прибавляется к окладам сотрудников (docs/TZ.md, раздел 32, «Детали 24.2»).
Норматив действует с месяца `valid_from` до следующего норматива той же статьи, вида и ЦФО; ЦФО пусто — для всех.
`per_point` (docs/TZ.md, раздел 32, «Детали 24.6») — норматив ветки действует на каждую точку торговли в ней:
процент — от базы точки, сумма — каждой точке; свой норматив точки заменяет общий.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NORM_KINDS = {"calc": "Расчётный", "fact": "Фактический", "norm": "Нормативный"}


class PlanNorm(Base):
    __tablename__ = "plan_norms"
    __table_args__ = (
        CheckConstraint("kind IN ('calc', 'fact', 'norm')", name="kind"),
        CheckConstraint("kind <> 'calc' OR (percent IS NOT NULL AND "
                        "(base_article_id IS NOT NULL OR base_form_code IS NOT NULL))", name="calc_fields"),
        CheckConstraint("kind NOT IN ('fact', 'norm') OR amount IS NOT NULL", name="fact_fields"),
        CheckConstraint("EXTRACT(DAY FROM valid_from) = 1", name="valid_from_month"),
        Index("uq_plan_norms_article_kind_cfo_month", "article_id", "kind", "cfo_id", "valid_from", unique=True,
              postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="CASCADE"))
    valid_from: Mapped[date] = mapped_column(Date)  # 1-е число месяца
    base_article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    base_form_code: Mapped[str | None] = mapped_column(String(30))  # код строки управленческой формы
    percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    per_point: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    comment: Mapped[str] = mapped_column(String(500), default="", server_default="")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
