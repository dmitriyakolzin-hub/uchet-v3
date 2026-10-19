from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import get_organization_for, org_permissions, visible_organization_ids
from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import LegalEntity, Operation, Organization, User
from app.schemas.organization import (
    LegalEntityCreate,
    LegalEntityOut,
    LegalEntityUpdate,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)

router = APIRouter(prefix="/api", tags=["Организации"])


def _commit(db: Session, conflict_message: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=conflict_message) from None


def _organization_out(db: Session, user: User, organization: Organization) -> OrganizationOut:
    out = OrganizationOut.model_validate(organization)
    out.permissions = sorted(org_permissions(db, user, organization.id))
    return out


# --- Организации ---


@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(user: CurrentUser, db: DbSession) -> list[OrganizationOut]:
    query = select(Organization).order_by(Organization.name)
    ids = visible_organization_ids(db, user)
    if ids is not None:
        query = query.where(Organization.id.in_(ids))
    return [_organization_out(db, user, o) for o in db.scalars(query)]


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
def create_organization(body: OrganizationCreate, user: AdminUser, db: DbSession) -> OrganizationOut:
    organization = Organization(**body.model_dump())
    db.add(organization)
    _commit(db, "Организация с таким наименованием уже есть")
    return _organization_out(db, user, organization)


@router.get("/organizations/{organization_id}", response_model=OrganizationOut)
def get_organization(organization_id: int, user: CurrentUser, db: DbSession) -> OrganizationOut:
    organization = get_organization_for(db, user, organization_id, Perm.VIEW)
    return _organization_out(db, user, organization)


@router.patch("/organizations/{organization_id}", response_model=OrganizationOut)
def update_organization(
    organization_id: int, body: OrganizationUpdate, user: CurrentUser, db: DbSession
) -> OrganizationOut:
    organization = get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    data = body.model_dump(exclude_unset=True)
    if "opening_balance_date" in data and data["opening_balance_date"] != organization.opening_balance_date:
        has_opening = db.scalar(
            select(Operation.id).where(Operation.organization_id == organization_id, Operation.is_opening).limit(1)
        )
        if has_opening:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Дату начальных остатков нельзя менять: остатки уже вводятся. Удалите операции ввода остатков",
            )
        organization.opening_balance_date = data.pop("opening_balance_date")
    for field, value in data.items():
        if value is not None:
            setattr(organization, field, value)
    _commit(db, "Организация с таким наименованием уже есть")
    return _organization_out(db, user, organization)


# --- Юрлица ---


@router.get("/organizations/{organization_id}/legal-entities", response_model=list[LegalEntityOut])
def list_legal_entities(organization_id: int, user: CurrentUser, db: DbSession) -> list[LegalEntity]:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    return list(
        db.scalars(
            select(LegalEntity)
            .where(LegalEntity.organization_id == organization_id)
            .order_by(LegalEntity.short_name)
        )
    )


@router.post(
    "/organizations/{organization_id}/legal-entities", response_model=LegalEntityOut, status_code=201
)
def create_legal_entity(
    organization_id: int, body: LegalEntityCreate, user: CurrentUser, db: DbSession
) -> LegalEntity:
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    entity = LegalEntity(organization_id=organization_id, **body.model_dump())
    db.add(entity)
    _commit(db, "Юрлицо с таким ИНН в этой организации уже есть")
    return entity


@router.patch("/legal-entities/{entity_id}", response_model=LegalEntityOut)
def update_legal_entity(
    entity_id: int, body: LegalEntityUpdate, user: CurrentUser, db: DbSession
) -> LegalEntity:
    entity = db.get(LegalEntity, entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено")
    try:
        get_organization_for(db, user, entity.organization_id, Perm.EDIT_REFERENCE)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Юрлицо не найдено") from None
        raise
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        # КПП и ОГРН можно очистить, передав null; обязательные поля null не принимают
        if value is not None or field in ("kpp", "ogrn"):
            setattr(entity, field, value)
    _commit(db, "Юрлицо с таким ИНН в этой организации уже есть")
    return entity
