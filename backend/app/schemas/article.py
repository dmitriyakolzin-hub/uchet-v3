from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]
Activity = Literal["operating", "investing", "financing"]
Direction = Literal["inflow", "outflow"]
Section = Literal["revenue", "cost", "selling", "admin", "other_income", "other_expense", "income_tax"]


class ArticleIn(BaseModel):
    """Статья целиком: при изменении передаются все поля (форма редактирует статью полностью)."""

    name: Name
    code: Code | None = None  # пусто — без кода
    parent_id: int | None = None  # родительская статья (подстатья наследует её признаки)
    sort_order: int = 0
    is_cash_flow: bool = False
    cash_flow_activity: Activity | None = None
    cash_flow_direction: Direction | None = None
    is_pl: bool = False
    pl_section: Section | None = None
    debit_account_id: int | None = None
    credit_account_id: int | None = None
    is_transfer: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _consistent(self):
        self.code = self.code or None
        if self.parent_id is not None:
            return self  # признаки подстатьи берутся от родителя
        if not (self.is_cash_flow or self.is_pl):
            raise ValueError("отметьте, где участвует статья: в ДДС, в ОПиУ или в обоих")
        if self.is_cash_flow and not (self.cash_flow_activity and self.cash_flow_direction):
            raise ValueError("для статьи ДДС укажите вид деятельности и направление")
        if self.is_pl and not self.pl_section:
            raise ValueError("для статьи ОПиУ укажите раздел")
        if not self.is_cash_flow:
            self.cash_flow_activity = self.cash_flow_direction = None
        if not self.is_pl:
            self.pl_section = None
        if (self.debit_account_id is None) != (self.credit_account_id is None):
            raise ValueError("в шаблоне проводки укажите оба счёта или ни одного")
        return self


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    parent_id: int | None
    code: str | None
    sort_order: int
    system_key: str | None
    name: str
    is_cash_flow: bool
    cash_flow_activity: Activity | None
    cash_flow_direction: Direction | None
    is_pl: bool
    pl_section: Section | None
    debit_account_id: int | None
    credit_account_id: int | None
    is_transfer: bool
    is_active: bool
