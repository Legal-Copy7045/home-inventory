"""Hide customer data from read-only users, in one place.

Rule: **read-only users never see customer information or customer repair
records** - not the customers, their equipment, its photos, documents,
service history, meter readings, NFC tags or history entries, nor repair
jobs.  Household inventory is unaffected.

Rather than repeating the check in every endpoint (and missing an alternate
path), a ``do_orm_execute`` hook adds ``with_loader_criteria`` filters to
every ORM query made by a session whose user may not view customers.  That
covers list queries, ``Session.get``, relationship loads, search, labels,
history - including endpoints added later.  :func:`auth.resolve_actor` marks
the request's session; sessions without the mark (background workers, the
integration acting as the system user) are not filtered.

The conditions use the *core* tables (``item_owners``, ``items``) rather than
ORM entities, so these criteria never filter their own sub-queries.
"""

from __future__ import annotations

from sqlalchemy import and_, event, exists, false, not_, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker, with_loader_criteria

from .models import (
    Attachment,
    Customer,
    CustomerContact,
    EquipmentProfile,
    HistoryEvent,
    Item,
    ItemOwner,
    ItemRelation,
    KitMember,
    Loan,
    MaintenanceTask,
    MeterReading,
    Product,
    ProductSource,
    RepairJob,
    RepairJobEvent,
    ReplenishmentProfile,
    ServiceEvent,
    ServicePart,
    ShoppingListItem,
    Tag,
)

FLAG = "can_view_customers"

_owners = ItemOwner.__table__
_items = Item.__table__


# Sub-queries use their own aliases so they never auto-correlate with an
# outer query over the same tables.


def _customer_item(col):
    """SQL: ``col`` is the id of a customer-owned item."""
    o = _owners.alias()
    return exists(select(o.c.item_id).where(o.c.item_id == col))


def _customer_only_product(col):
    """SQL: product ``col`` has customer equipment and no household holding."""
    i1, o1, i2, o2 = _items.alias(), _owners.alias(), _items.alias(), _owners.alias()
    has_customer = exists(select(i1.c.id).select_from(i1.join(o1, o1.c.item_id == i1.c.id)).where(i1.c.product_id == col))
    has_household = exists(
        select(i2.c.id).where(i2.c.product_id == col, ~exists(select(o2.c.item_id).where(o2.c.item_id == i2.c.id)))
    )
    return and_(has_customer, not_(has_household))


def _household_service_event(col):
    """SQL: service event ``col`` is not part of a repair job or customer equipment."""
    se = ServiceEvent.__table__.alias()
    return exists(select(se.c.id).where(
        se.c.id == col, se.c.repair_job_id.is_(None), or_(se.c.item_id.is_(None), ~_customer_item(se.c.item_id))
    ))


def _criteria():
    return [
        with_loader_criteria(Item, ~_customer_item(Item.id)),
        with_loader_criteria(Product, ~_customer_only_product(Product.id)),
        with_loader_criteria(Attachment, and_(
            Attachment.repair_job_id.is_(None),
            or_(Attachment.item_id.is_(None), ~_customer_item(Attachment.item_id)),
            or_(Attachment.product_id.is_(None), ~_customer_only_product(Attachment.product_id)),
        )),
        with_loader_criteria(ServiceEvent, and_(
            ServiceEvent.repair_job_id.is_(None),
            or_(ServiceEvent.item_id.is_(None), ~_customer_item(ServiceEvent.item_id)),
        )),
        with_loader_criteria(ServicePart, _household_service_event(ServicePart.service_event_id)),
        with_loader_criteria(MeterReading, ~_customer_item(MeterReading.item_id)),
        with_loader_criteria(MaintenanceTask, ~_customer_item(MaintenanceTask.item_id)),
        with_loader_criteria(EquipmentProfile, ~_customer_item(EquipmentProfile.item_id)),
        with_loader_criteria(Loan, ~_customer_item(Loan.item_id)),
        with_loader_criteria(ItemRelation, and_(~_customer_item(ItemRelation.from_item_id), ~_customer_item(ItemRelation.to_item_id))),
        with_loader_criteria(KitMember, ~_customer_item(KitMember.item_id)),
        with_loader_criteria(Tag, or_(Tag.target_type != "item", ~_customer_item(Tag.target_id))),
        with_loader_criteria(ProductSource, ~_customer_only_product(ProductSource.product_id)),
        with_loader_criteria(ReplenishmentProfile, ~_customer_only_product(ReplenishmentProfile.product_id)),
        with_loader_criteria(ShoppingListItem, ~_customer_only_product(ShoppingListItem.product_id)),
        with_loader_criteria(HistoryEvent, and_(
            HistoryEvent.entity_type != "customer",
            ~HistoryEvent.action.like("repair\\_%", escape="\\"),
            ~HistoryEvent.action.like("owner\\_%", escape="\\"),
            or_(HistoryEvent.entity_type != "item", ~_customer_item(HistoryEvent.entity_id)),
        )),
        with_loader_criteria(Customer, false()),
        with_loader_criteria(CustomerContact, false()),
        with_loader_criteria(RepairJob, false()),
        with_loader_criteria(RepairJobEvent, false()),
    ]


def install(factory: sessionmaker) -> None:
    @event.listens_for(factory, "do_orm_execute")
    def _hide_customer_data(state: ORMExecuteState) -> None:
        if state.session.info.get(FLAG, True):
            return  # members, administrators, background work
        if state.is_select or state.is_relationship_load:
            state.statement = state.statement.options(*_criteria())


def can_view_customers(db: Session) -> bool:
    return bool(db.info.get(FLAG, True))
