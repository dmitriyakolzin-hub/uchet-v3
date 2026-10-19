from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Membership(Base):
    """Роль пользователя в организации. Одна роль на пару «пользователь — организация»."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('finance_director', 'ceo', 'cfo_head')", name="role"),
        CheckConstraint("cfo_id IS NULL OR role = 'cfo_head'", name="cfo_only_for_cfo_head"),
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
