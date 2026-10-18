"""Общий порядок работы со справочниками организации.

Для каждого справочника регистрируются три эндпоинта:
  GET   /api/organizations/{organization_id}/<path>   — список (право «просмотр»)
  POST  /api/organizations/{organization_id}/<path>   — создать (право «изменение справочников»)
  PATCH /api/<path>/{item_id}                          — изменить (то же право в организации записи)
Ссылки на другие справочники проверяются: записи организации — только из этой же организации.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import User


@dataclass(frozen=True)
class Ref:
    """Поле-ссылка: модель, на которую оно указывает, и название для сообщения об ошибке."""

    model: type
    title: str
    org_scoped: bool = True


@dataclass
class OrgResource:
    path: str
    model: type
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    out_schema: type[BaseModel]
    title: str  # «Контрагент» — для сообщений
    conflict_message: str
    order_by: Callable[[type], list]
    refs: dict[str, Ref] = field(default_factory=dict)
    nullable_fields: set[str] = field(default_factory=set)
    # дополнительная проверка и подготовка записи после применения изменений:
    # check(db, obj, creating); ValueError → 422 с текстом ошибки
    check: Callable[[Session, object, bool], None] | None = None


def _check_refs(db: Session, organization_id: int, data: dict, refs: dict[str, Ref]) -> None:
    for name, ref in refs.items():
        value = data.get(name)
        if value is None:
            continue
        obj = db.get(ref.model, value)
        if obj is None or (ref.org_scoped and obj.organization_id != organization_id):
            where = " в этой организации" if ref.org_scoped else ""
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{ref.title}: не найдено{where}"
            )


def _save(db: Session, resource: OrgResource, obj, creating: bool) -> None:
    if resource.check is not None:
        try:
            # без автозаписи: новая запись не должна уйти в базу раньше, чем проверка её заполнит
            with db.no_autoflush:
                resource.check(db, obj, creating)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=resource.conflict_message) from None
    db.refresh(obj)


def _get_item(db: Session, user: User, resource: OrgResource, item_id: int, perm: Perm):
    obj = db.get(resource.model, item_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{resource.title}: не найдено")
    if obj is None:
        raise not_found
    try:
        get_organization_for(db, user, obj.organization_id, perm)
    except HTTPException as exc:
        # чужая организация — «не найдено», чтобы не раскрывать, что запись существует
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return obj


def register(router: APIRouter, resource: OrgResource) -> None:
    Model = resource.model
    Create = resource.create_schema
    Update = resource.update_schema
    Out = resource.out_schema

    def list_items(organization_id: int, user: CurrentUser, db: DbSession):
        get_organization_for(db, user, organization_id, Perm.VIEW)
        query = (
            select(Model)
            .where(Model.organization_id == organization_id)
            .order_by(*resource.order_by(Model))
        )
        return list(db.scalars(query))

    def create_item(organization_id: int, body: Create, user: CurrentUser, db: DbSession):  # type: ignore[valid-type]
        get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
        data = body.model_dump()
        _check_refs(db, organization_id, data, resource.refs)
        obj = Model(organization_id=organization_id, **data)
        db.add(obj)
        _save(db, resource, obj, creating=True)
        return obj

    def update_item(item_id: int, body: Update, user: CurrentUser, db: DbSession):  # type: ignore[valid-type]
        obj = _get_item(db, user, resource, item_id, Perm.EDIT_REFERENCE)
        data = body.model_dump(exclude_unset=True)
        _check_refs(db, obj.organization_id, data, resource.refs)
        for name, value in data.items():
            if value is not None or name in resource.nullable_fields:
                setattr(obj, name, value)
        _save(db, resource, obj, creating=False)
        return obj

    collection = f"/organizations/{{organization_id}}/{resource.path}"
    suffix = resource.path.replace("-", "_")
    router.add_api_route(
        collection, list_items, methods=["GET"], response_model=list[Out], name=f"list_{suffix}"
    )
    router.add_api_route(
        collection, create_item, methods=["POST"], response_model=Out, status_code=201,
        name=f"create_{suffix}",
    )
    router.add_api_route(
        f"/{resource.path}/{{item_id}}", update_item, methods=["PATCH"], response_model=Out,
        name=f"update_{suffix}",
    )
