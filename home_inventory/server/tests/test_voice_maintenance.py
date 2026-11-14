"""Spoken maintenance answers (the voice intents ask these)."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_customers import _customer, _job
from tests.test_parts import _set

TODAY = date.today()


def _equip(u, name, etype, maker=None, model=None, hours=None, **kw):
    item = u.item(name, manufacturer=maker, model_number=model, **kw)
    body = {"equipment_type": etype, "meter": "hours" if hours is not None else "none"}
    if hours is not None:
        body["current_hours"] = hours
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json=body)
    return item


def _task(u, item, title, **kw):
    return u.ok("post", f"/api/v1/items/{item['id']}/maintenance-tasks", json={"title": title, **kw})


def _say(u, **params):
    return u.ok("get", "/api/v1/voice/maintenance", params=params)


def _garage(u):
    mower = _equip(u, "Toro Recycler 22-inch Personal Pace", "Lawn mower", "Toro", "20333", hours=45)
    gen = _equip(u, "Honda EU2200i", "Generator", "Honda", "EU2200i")
    snow = _equip(u, "Ariens Deluxe 28", "Snowblower", "Ariens")
    oil = _task(u, mower, "Oil change", interval_hours=50, last_done_hours=0)  # due in 5 h
    blade = _task(u, mower, "Blade inspection", interval_days=60, last_done_on=(TODAY - timedelta(days=40)).isoformat())
    air = _task(u, gen, "Air filter inspection", interval_days=90, last_done_on=(TODAY - timedelta(days=50)).isoformat())
    prep = _task(u, snow, "Seasonal preparation", interval_days=365, last_done_on=(TODAY - timedelta(days=380)).isoformat())
    return mower, gen, snow, oil, blade, air, prep


def test_general_upcoming_is_ordered_by_urgency(env):
    u = env.admin
    _garage(u)
    a = _say(u, kind="due", range="upcoming")["speech"]
    assert a.startswith("You have 3 maintenance tasks coming up.")
    # overdue first, then by how soon
    assert a.index("Ariens snowblower seasonal preparation is overdue by 15 days") < a.index("Toro lawn mower oil change")
    assert "within the next 5 operating hours" in a
    assert "air filter" not in a.lower()  # 40 days away: not within a month
    assert "Honda generator air filter inspection is due next month" in _say(u, kind="due", range="all")["speech"]
    assert _say(u, kind="due", range="overdue")["speech"] == (
        "You have 1 maintenance task overdue. The Ariens snowblower seasonal preparation is overdue by 15 days.")
    assert _say(u, kind="due", range="today")["speech"].startswith("You have 1 maintenance task due today.")


def test_equipment_specific_with_stock(env):
    u = env.admin
    mower, *_rest = _garage(u)
    oil_task = _rest[2]
    plug = u.item("Spark plug RJ19LM", quantity=2)
    filt = u.item("Air filter 36905", quantity=0)
    _set(u, oil_task, [{"product_id": plug["product_id"], "quantity": 1}, {"product_id": filt["product_id"], "quantity": 1}])
    r = _say(u, kind="due", equipment="the Toro lawn mower")
    assert r["equipment"]["id"] == mower["id"]
    s = r["speech"]
    assert s.startswith("Your Toro lawn mower has 2 maintenance tasks coming up.")
    assert "The oil change is due within the next 5 operating hours." in s
    assert "The blade inspection is due in 2 weeks." in s
    assert "You have the spark plug rj19lm in stock." in s and "You need to buy air filter 36905." in s
    # Other names for the same mower
    for words in ("mower", "lawnmower", "Toro", "recycler", "20333"):
        assert _say(u, kind="due", equipment=words)["equipment"]["id"] == mower["id"], words
    assert _say(u, kind="due", equipment="tractor")["speech"] == "I couldn't find any equipment called tractor."


def test_ambiguous_equipment_asks_which(env):
    u = env.admin
    a = _equip(u, "Honda EU2200i", "Generator", "Honda")
    b = _equip(u, "Champion 4500", "Generator", "Champion")
    r = _say(u, kind="due", equipment="generator")
    assert {c["id"] for c in r["choose"]} == {a["id"], b["id"]} and r["speech"].startswith("Which one:")
    assert "equipment" not in r
    # Answered by id afterwards
    assert _say(u, kind="due", equipment_id=a["id"])["equipment"]["name"] == "Honda generator"
    # A clear full match wins over partial ones
    assert _say(u, kind="due", equipment="Honda generator")["equipment"]["id"] == a["id"]


def test_last_done_parts_attention_recent(env):
    u = env.admin
    mower, gen, snow, oil, blade, air, prep = _garage(u)
    u.ok("post", f"/api/v1/maintenance-tasks/{oil['id']}/complete",
         json={"performed_on": (TODAY - timedelta(days=3)).isoformat(), "hours": 44})
    s = _say(u, kind="last_done", equipment="mower", task="change the oil")["speech"]
    assert s.startswith("The last oil change on your Toro lawn mower was on") and "at 44 operating hours" in s
    # The machine can come in the job's words ("change the oil in the mower").
    assert _say(u, kind="last_done", task="change the oil in the mower")["speech"] == s
    assert _say(u, kind="last_done", equipment="mower", task="sharpen the blades")["speech"] == (
        "I have no record of sharpen the blades on your Toro lawn mower.")
    # Parts for the next service
    stab = u.item("Fuel stabiliser", quantity=0)
    _set(u, prep, [{"product_id": stab["product_id"], "quantity": 1}])
    assert _say(u, kind="have_parts", equipment="snowblower")["speech"] == (
        "Not quite. For your Ariens snowblower you need to buy fuel stabiliser.")
    assert "you need fuel stabiliser" in _say(u, kind="parts", equipment="snowblower")["speech"]
    u.ok("post", f"/api/v1/items/{stab['id']}/adjust", json={"delta": 1})
    assert _say(u, kind="have_parts", equipment="snowblower")["speech"].startswith("Yes, you have everything")
    # Needs attention / recently completed
    att = _say(u, kind="attention")["speech"]
    assert att.startswith("1 piece of equipment needs attention: the Ariens snowblower has an overdue job")
    assert _say(u, kind="recent")["speech"].startswith("In the last month you completed 1 job: oil change on the Toro")


def test_voice_never_includes_customer_equipment(env):
    u = env.admin
    _job(u, _customer(u))  # a customer's Toro mower in for repair
    prof_item = u.ok("get", "/api/v1/repair-jobs")[0]["item"]
    u.ok("put", f"/api/v1/items/{prof_item['id']}/equipment", json={"equipment_type": "Lawn mower"})
    assert _say(u, kind="due", equipment="Toro")["speech"] == "I couldn't find any equipment called Toro."
    assert _say(u, kind="due", equipment_id=prof_item["id"])["speech"] == "I couldn't find that equipment."
    hook = env.integration()  # no Home Assistant user: same answer
    assert hook.ok("get", "/api/v1/voice/maintenance", params={"kind": "attention"})["speech"].startswith("Nothing needs")
