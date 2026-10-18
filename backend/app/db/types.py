from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType):
    """Путь в дереве PostgreSQL (расширение ltree), например «12.34.56» — id узлов от корня."""

    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "LTREE"


# Чтобы при чтении схемы из базы (сверка моделей с миграциями) ltree распознавался, а не терялся
ischema_names["ltree"] = Ltree
