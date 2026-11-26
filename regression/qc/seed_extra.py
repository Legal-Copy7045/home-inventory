"""Records the demo seed (tools/demo/seed.py) leaves out, so every page has something to show.

    python seed_extra.py http://127.0.0.1:18199/

Prints one JSON line of IDs.  All names are made up.
"""
import io
import json
import sys

import httpx
from PIL import Image

BASE = sys.argv[1] + "api/v1/"
H = {"X-Remote-User-Id": "qc-admin", "X-Remote-User-Name": "morgan", "X-Remote-User-Display-Name": "Morgan"}
c = httpx.Client(base_url=BASE, headers=H, timeout=60, trust_env=False)


def ok(r):
    if r.status_code >= 400:
        sys.exit(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:300]}")
    return r.json() if r.content else None


def first(path, **params):
    d = ok(c.get(path, params=params))
    rows = d.get("items", d) if isinstance(d, dict) else d
    return rows[0] if rows else None


def jpeg(w=1000, h=700, colour=(220, 226, 232)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


ids = {}
loc = first("locations")
cont = first("containers")
item = first("items")
ids.update(location=loc["id"], container=cont["id"], item=item["id"])

cust = ok(c.post("customers", json={"name": "Sam Hillcrest", "phone": "555-0100", "address": "12 Lakeview Road, Lakeview"}))
ids["customer"] = cust["id"]
job = ok(c.post("repair-jobs", json={"customer_id": cust["id"], "reported_problem": "Will not start after winter",
                                      "new_equipment": {"name": "Push mower", "manufacturer": "Example Co"}}))
ids["repair"] = job["id"]

kit = ok(c.post("kits", json={"name": "Camping kit", "description": "Everything for a weekend away", "item_ids": [item["id"]]}))
ids["kit"] = kit["id"]

photo = ok(c.post("attachments", files={"file": ("plan.jpg", jpeg(), "image/jpeg")}, data={}))
mp = ok(c.post("maps", json={"title": "Garage plan", "location_id": loc["id"], "photo_id": photo["id"]}))
ids["map"] = mp["id"]

tag = ok(c.post("tags", json={"target_type": "container", "target_id": cont["id"], "label": "Tote tag"}))
ids["tag"] = tag.get("public_id") or tag.get("ref") or tag.get("code") or tag["id"]

ver = ok(c.post("verifications", json={"container_id": cont["id"]}))
ids["verification"] = ver["id"]

print(json.dumps(ids))
