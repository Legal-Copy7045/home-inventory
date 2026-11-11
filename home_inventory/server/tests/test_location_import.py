"""Bulk location import from Excel/CSV."""

import io

from openpyxl import Workbook, load_workbook

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Locations"
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _upload(u, data, confirm=False, name="locations.xlsx"):
    return u.post("/api/v1/locations/import", files={"file": (name, data, XLSX)}, data={"confirm": str(confirm).lower()})


HEADER = ["Level 1", "Level 2", "Level 3", "Level 4", "Kind", "Description"]


def test_template_download_and_round_trip(env):
    u = env.admin
    r = u.get("/api/v1/locations/template.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith(XLSX)
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Locations", "How to use"]
    assert [c.value for c in wb["Locations"][1]][:2] == ["Level 1", "Level 2"]
    # The example template imports as-is
    res = _upload(u, r.content, confirm=True).json()
    assert res["applied"] and res["created"] >= 10 and not res["errors"]
    tree = u.ok("get", "/api/v1/locations")
    assert any(loc["path"] == "Property › Shed › Rear Right Shelving › Middle Shelf" for loc in tree)
    # Pre-filled export contains the existing hierarchy and re-imports without changes
    r2 = u.get("/api/v1/locations/template.xlsx", params={"existing": "true"})
    res2 = _upload(u, r2.content, confirm=True).json()
    assert res2["created"] == 0 and res2["create_count"] == 0


def test_preview_then_apply_full_paths(env):
    u = env.admin
    shed = u.location("Shed")  # existing; must be reused, not duplicated
    data = _xlsx([
        HEADER,
        ["Shed", "Rear Right Shelving", "Middle Shelf", None, "Shelf", "Hoses"],
        ["shed", "Rear Right Shelving", "Top Shelf", None, None, None],
        ["House", "Garage", None, None, "Room", None],
        [None, None, None, None, None, None],  # blank row ignored
    ])
    preview = _upload(u, data).json()
    assert preview["applied"] is False
    assert preview["to_create"] == [
        "Shed › Rear Right Shelving",
        "Shed › Rear Right Shelving › Middle Shelf",
        "Shed › Rear Right Shelving › Top Shelf",
        "House",
        "House › Garage",
    ]
    assert preview["existing_count"] == 1
    assert len(u.ok("get", "/api/v1/locations")) == 1  # nothing created yet
    res = _upload(u, data, confirm=True).json()
    assert res["created"] == 5
    locs = {loc["path"]: loc for loc in u.ok("get", "/api/v1/locations")}
    middle = u.ok("get", f"/api/v1/locations/{locs['Shed › Rear Right Shelving › Middle Shelf']['id']}")
    assert middle["kind"] == "Shelf" and middle["description"] == "Hoses" and len(middle["qr_code"]) == 8
    assert locs["Shed › Rear Right Shelving"]["parent_id"] == shed["id"]
    # Second import: idempotent
    again = _upload(u, data, confirm=True).json()
    assert again["created"] == 0
    hist = u.ok("get", "/api/v1/history", params={"action": "imported"})["events"]
    assert "5 created" in hist[0]["summary"]


def test_outline_style_and_errors(env):
    u = env.admin
    data = _xlsx([
        ["My locations"],  # title row above the header is fine
        HEADER,
        ["Property", None, None, None],
        [None, "House", None, None],
        [None, None, "Garage", None],
        [None, None, "Basement", None],
        [None, "Shed", None, None],
        ["Cottage", None, "Kitchen", None],  # gap -> error
        [None, None, None, "Orphan"],  # Level 4 under a 2-level path: no parent
    ])
    res = _upload(u, data).json()
    assert "Property › House › Garage" in res["to_create"]
    assert "Property › House › Basement" in res["to_create"]
    assert "Property › Shed" in res["to_create"]
    assert res["errors"][0] == {"row": 8, "message": "Levels must be filled left to right without gaps"}
    assert res["errors"][1]["row"] == 9 and "no parent" in res["errors"][1]["message"]


def test_csv_and_bad_files(env):
    u = env.admin
    csv_data = b"Level 1,Level 2,Kind\nGarage,Workbench,Workbench\n"
    res = _upload(u, csv_data, confirm=True, name="locs.csv").json()
    assert res["created"] == 2
    assert _upload(u, b"", name="x.xlsx").status_code == 422
    assert _upload(u, b"not a workbook", name="x.xlsx").status_code == 422
    assert "Level 1" in _upload(u, _xlsx([["Name"], ["Shed"]])).json()["detail"]
    assert _upload(u, b"\xd0\xcf\x11\xe0junk", name="old.xls").status_code == 422


def test_read_only_cannot_import(env):
    viewer = env.user("u-view", "Viewer Person")
    viewer.get("/api/v1/me")
    users = env.admin.ok("get", "/api/v1/users")
    vid = next(x["id"] for x in users if x["name"] == "Viewer Person")
    env.admin.ok("patch", f"/api/v1/users/{vid}", json={"role": "read_only"})
    assert _upload(viewer, _xlsx([HEADER, ["Shed"]])).status_code == 403
    assert viewer.get("/api/v1/locations/template.xlsx").status_code == 200
