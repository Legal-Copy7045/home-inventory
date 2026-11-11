"""Physical placement: hierarchical locations, nested containers, full paths
and circular-relationship prevention."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Container, Item, Location

SEPARATOR = " › "
MAX_DEPTH = 64


class PlacementError(ValueError):
    """Raised for invalid placements (cycles, missing or deleted targets)."""


@dataclass
class Resolver:
    """Resolves full paths with the whole location/container graph loaded
    once.  Households have at most a few thousand of each, so this is far
    cheaper than per-row recursive queries."""

    locations: dict[int, Location] = field(default_factory=dict)
    containers: dict[int, Container] = field(default_factory=dict)

    @classmethod
    def load(cls, db: Session) -> Resolver:
        r = cls()
        r.locations = {loc.id: loc for loc in db.scalars(select(Location))}
        r.containers = {c.id: c for c in db.scalars(select(Container))}
        return r

    # -- locations -------------------------------------------------------
    def location_chain(self, location_id: int | None) -> list[Location]:
        chain: list[Location] = []
        seen: set[int] = set()
        current = self.locations.get(location_id) if location_id else None
        while current is not None and current.id not in seen and len(chain) < MAX_DEPTH:
            seen.add(current.id)
            chain.append(current)
            current = self.locations.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        return chain

    def location_path(self, location_id: int | None) -> str:
        return SEPARATOR.join(loc.name for loc in self.location_chain(location_id))

    def descendant_location_ids(self, location_id: int) -> set[int]:
        children: dict[int, list[int]] = {}
        for loc in self.locations.values():
            if loc.parent_id:
                children.setdefault(loc.parent_id, []).append(loc.id)
        out: set[int] = set()
        stack = [location_id]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(children.get(cur, []))
        return out

    # -- containers ------------------------------------------------------
    def container_chain(self, container_id: int | None) -> list[Container]:
        """Containers from outermost to ``container_id`` (inclusive)."""
        chain: list[Container] = []
        seen: set[int] = set()
        current = self.containers.get(container_id) if container_id else None
        while current is not None and current.id not in seen and len(chain) < MAX_DEPTH:
            seen.add(current.id)
            chain.append(current)
            current = self.containers.get(current.parent_container_id) if current.parent_container_id else None
        chain.reverse()
        return chain

    def container_effective_location_id(self, container_id: int | None) -> int | None:
        chain = self.container_chain(container_id)
        return chain[0].location_id if chain else None

    def descendant_container_ids(self, container_id: int) -> set[int]:
        children: dict[int, list[int]] = {}
        for c in self.containers.values():
            if c.parent_container_id:
                children.setdefault(c.parent_container_id, []).append(c.id)
        out: set[int] = set()
        stack = [container_id]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(children.get(cur, []))
        return out

    def containers_in_location_tree(self, location_id: int) -> set[int]:
        loc_ids = self.descendant_location_ids(location_id)
        return {
            c.id for c in self.containers.values() if self.container_effective_location_id(c.id) in loc_ids
        }

    # -- descriptions ----------------------------------------------------
    def describe(self, container_id: int | None, location_id: int | None) -> dict[str, Any]:
        containers = self.container_chain(container_id) if container_id else []
        base_location_id = containers[0].location_id if containers else location_id
        locations = self.location_chain(base_location_id)
        parts = [loc.name for loc in locations] + [_container_label(c) for c in containers]
        return {
            "container": _container_ref(containers[-1]) if containers else None,
            "container_path": [_container_ref(c) for c in containers],
            "location": {"id": locations[-1].id, "name": locations[-1].name} if locations else None,
            "location_path": [{"id": loc.id, "name": loc.name} for loc in locations],
            "effective_location_id": base_location_id,
            "path": SEPARATOR.join(parts) if parts else "Unassigned",
        }

    def describe_item(self, item: Item) -> dict[str, Any]:
        return self.describe(item.container_id, item.location_id)

    def describe_item_home(self, item: Item) -> dict[str, Any] | None:
        if item.home_container_id is None and item.home_location_id is None:
            return None
        return self.describe(item.home_container_id, item.home_location_id)


def _container_label(c: Container) -> str:
    if c.number and c.number not in c.name:
        return f"{c.name} #{c.number}"
    return c.name


def _container_ref(c: Container) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "number": c.number, "qr_code": c.qr_code, "label": _container_label(c)}


# -- validation -------------------------------------------------------------


def validate_location_parent(db: Session, location_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = db.get(Location, parent_id)
    if parent is None or parent.deleted_at is not None:
        raise PlacementError("Parent location does not exist")
    if location_id is None:
        return
    if parent_id == location_id:
        raise PlacementError("A location cannot be inside itself")
    r = Resolver.load(db)
    if parent_id in r.descendant_location_ids(location_id):
        raise PlacementError("A location cannot be moved inside one of its own sub-locations")


def validate_container_target(
    db: Session, container_id: int | None, location_id: int | None, parent_container_id: int | None
) -> None:
    if location_id is not None and parent_container_id is not None:
        raise PlacementError("Choose either a location or a parent container, not both")
    if location_id is not None:
        loc = db.get(Location, location_id)
        if loc is None or loc.deleted_at is not None:
            raise PlacementError("Location does not exist")
    if parent_container_id is not None:
        parent = db.get(Container, parent_container_id)
        if parent is None or parent.deleted_at is not None:
            raise PlacementError("Parent container does not exist")
        if container_id is not None:
            if parent_container_id == container_id:
                raise PlacementError("A container cannot be placed inside itself")
            r = Resolver.load(db)
            if parent_container_id in r.descendant_container_ids(container_id):
                raise PlacementError("A container cannot be placed inside a container it already holds")


def validate_item_target(db: Session, container_id: int | None, location_id: int | None) -> None:
    if container_id is not None and location_id is not None:
        raise PlacementError("Choose either a container or a location, not both")
    if container_id is not None:
        c = db.get(Container, container_id)
        if c is None or c.deleted_at is not None:
            raise PlacementError("Container does not exist")
    if location_id is not None:
        loc = db.get(Location, location_id)
        if loc is None or loc.deleted_at is not None:
            raise PlacementError("Location does not exist")
