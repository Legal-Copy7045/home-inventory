"""Shelf check-ups: which place most needs a fresh photo inventory."""

from datetime import UTC, datetime, timedelta

from home_inventory_server.models import ContainerScan, ScanStatus
from home_inventory_server.services import checkups


def test_never_checked_places_with_most_things_come_first_then_the_oldest(env):
    u = env.admin
    big = u.location("Garage wall")
    small = u.location("Drawer")
    done = u.location("Top shelf")
    for i in range(3):
        u.item(f"Thing {i}", location_id=big["id"])
    u.item("Pen", location_id=small["id"])
    u.item("Paint", location_id=done["id"])
    with env.state.db.SessionLocal() as db:
        db.add(ContainerScan(location_id=done["id"], status=ScanStatus.confirmed,
                             confirmed_at=datetime.now(UTC) - timedelta(days=120)))
        db.commit()
    places = u.ok("get", "/api/v1/checkups")["places"]
    assert [p["name"] for p in places] == ["Garage wall", "Drawer", "Top shelf"]
    assert places[0]["things"] == 3 and places[0]["last_checked"] is None and places[2]["days"] == 120


def test_monthly_notice_is_published_once_a_month(env):
    u = env.admin
    shelf = u.location("Shelf")
    u.item("Glue", location_id=shelf["id"])
    with env.state.db.SessionLocal() as db:
        first = checkups.monthly_notice(db)
        db.commit()
        assert first["name"] == "Shelf"
        assert checkups.monthly_notice(db) is None  # not again this month
