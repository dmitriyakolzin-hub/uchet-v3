from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Membership(Base):
    """Роль пользователя в организации. Одна роль на пару «пользователь — организация»."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('finance_director', 'finance_manager', 'ceo', 'cfo_head', 'executor_head', "
                        "'accountant', 'engineer', 'aho_operator', 'mol', 'employee')", name="role"),
        CheckConstraint("cfo_id IS NULL OR role = 'cfo_head'", name="cfo_only_for_cfo_head"),
        CheckConstraint("executor_id IS NULL OR role = 'executor_head'", name="executor_only_for_executor_head"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(30))
    # Руководитель ЦФО: корень его ветки дерева ЦФО (docs/TZ.md, раздел 13)
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"), index=True)
    # Руководитель ЦФУ: его ЦФУ (отдел — вместе с исполнителями), docs/TZ.md, раздел 24
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="RESTRICT"), index=True)
    # дополнительный просмотр (docs/TZ.md, раздел 30, «Детали 22.6»): поверх роли — без права действовать
    see_all_requests: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    see_all_assets: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
