"""Seed a fictional household into a fresh Home Inventory for the demo video."""
import io, json, os, sys, random
import httpx
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

BASE = os.environ.get("DEMO_BASE", "http://localhost:18099/") + "api/v1/"
H = {"X-Remote-User-Id": "demo-admin", "X-Remote-User-Name": "alex", "X-Remote-User-Display-Name": "Alex"}
c = httpx.Client(base_url=BASE, headers=H, timeout=60)

def ok(r):
    if r.status_code >= 400:
        sys.exit(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:300]}")
    return r.json() if r.content else None

def post(p, **kw): return ok(c.post(p, **kw))
def put(p, **kw): return ok(c.put(p, **kw))
def patch(p, **kw): return ok(c.patch(p, **kw))
def get(p, **kw): return ok(c.get(p, **kw))

try:
    FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
    SMALL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
except OSError:
    FONT = SMALL = ImageFont.load_default()

PALETTE = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (241, 196, 15), (155, 89, 182), (230, 126, 34), (26, 188, 156), (52, 73, 94)]

def photo(label, seed):
    rnd = random.Random(seed)
    base = PALETTE[seed % len(PALETTE)]
    img = Image.new("RGB", (800, 600), (238, 240, 243))
    d = ImageDraw.Draw(img)
    # a simple "object" shape so photos look different
    shape = seed % 3
    col = tuple(min(255, int(v * 0.9)) for v in base)
    if shape == 0:
        d.rounded_rectangle((180, 120, 620, 430), 40, fill=col)
    elif shape == 1:
        d.ellipse((230, 90, 570, 430), fill=col)
    else:
        d.polygon([(400, 80), (640, 430), (160, 430)], fill=col)
    d.rounded_rectangle((60, 460, 740, 560), 20, fill=(255, 255, 255))
    w = d.textlength(label, font=SMALL)
    d.text(((800 - w) / 2, 492), label, fill=(40, 40, 40), font=SMALL)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=88); return buf.getvalue()

def upload(data, filename, **form):
    mime = "application/pdf" if filename.endswith(".pdf") else "image/jpeg"
    return post("attachments", files={"file": (filename, data, mime)}, data={k: str(v) for k, v in form.items()})

seed_n = [0]
def item(name, place, qty=1, photo_label=None, **kw):
    seed_n[0] += 1
    ph = upload(photo(photo_label or name, seed_n[0]), "photo.jpg")
    body = {"name": name, "quantity": qty, "photo_ids": [ph["id"]], **kw}
    body.update(place)
    return post("items", json=body)["items"][0]

for cat in ("Tools", "Garden", "Electrical", "Automotive", "Hardware", "Household", "Camping"):
    c.post("categories", json={"name": cat})

# Places
house = post("locations", json={"name": "House"})
garage = post("locations", json={"name": "Garage"})
shed = post("locations", json={"name": "Garden shed"})
loft = post("locations", json={"name": "Loft"})
kitchen = post("locations", json={"name": "Kitchen", "parent_id": house["id"]})
utility = post("locations", json={"name": "Utility room", "parent_id": house["id"]})
g_shelf = post("locations", json={"name": "Workbench shelf", "parent_id": garage["id"]})
g_rack = post("locations", json={"name": "Metal racking", "parent_id": garage["id"]})
L = lambda loc: {"location_id": loc["id"]}

tote7 = post("containers", json={"name": "Blue tote", "number": "7", "container_type": "Tote", "location_id": g_rack["id"], "description": "Power tools and accessories"})
tote8 = post("containers", json={"name": "Clear tote", "number": "8", "container_type": "Tote", "location_id": g_rack["id"], "description": "Electrical and lighting"})
box12 = post("containers", json={"name": "Camping box", "number": "12", "container_type": "Box", "location_id": loft["id"]})
drawer = post("containers", json={"name": "Screw organiser", "number": "3", "container_type": "Drawer unit", "location_id": g_shelf["id"]})
C = lambda box: {"container_id": box["id"]}

drill = item("Cordless drill DCD777", C(tote7), manufacturer="DeWalt", model_number="DCD777", category="Tools", serial_number="DW-2231-889", purchase_price="99.00", purchase_date="2024-03-02", retailer="Lowe's")
item("Impact driver DCF787", C(tote7), manufacturer="DeWalt", model_number="DCF787", category="Tools")
item("18V battery 2Ah", C(tote7), qty=3, manufacturer="DeWalt", model_number="DCB203", category="Tools", min_quantity=2)
item("Drill bit set", C(tote7), category="Tools")
item("Jigsaw", C(tote7), manufacturer="Bosch", category="Tools")
item("Extension cord 25 ft", C(tote8), qty=2, category="Electrical")
item("LED work light", C(tote8), category="Electrical")
item("Outdoor string lights", C(tote8), category="Electrical")
item("Wood screws 2 in", C(drawer), qty=120, category="Hardware", min_quantity=50)
item("Wall plugs", C(drawer), qty=60, category="Hardware")
item("Cable ties", C(drawer), qty=200, category="Hardware")
item("Tent 4-person", C(box12), category="Camping")
item("Camping stove", C(box12), category="Camping")
item("Head torch", C(box12), qty=2, category="Camping")
item("Hammer", L(g_shelf), category="Tools")
item("Socket set", L(g_shelf), manufacturer="Craftsman", category="Tools")
item("Stud finder", L(g_shelf), category="Tools")
item("Tape measure", L(g_shelf), qty=2, category="Tools")
item("Hedge trimmer", L(shed), manufacturer="Stihl", category="Garden")
item("Garden hose 50 ft", L(shed), category="Garden")
item("Leaf blower", L(shed), manufacturer="Ryobi", category="Garden")
item("Pressure washer", L(garage), manufacturer="Karcher", model_number="K2", category="Garden")
item("Paper towels", L(utility), qty=1, category="Household", min_quantity=4)
item("AA batteries", L(utility), qty=6, category="Household", min_quantity=8)
item("Light bulbs E27", L(utility), qty=3, category="Household")
item("Slow cooker", L(kitchen), category="Household")
item("Stand mixer", L(kitchen), manufacturer="KitchenAid", category="Household", purchase_price="349.00", purchase_date="2023-11-24")
# Duplicates (recorded twice)
item("WD-40 Multi-Use Product", L(g_shelf), manufacturer="WD-40 Company", category="Hardware", photo_label="WD-40 (garage)")
item("WD-40 Multi-Use Product", L(g_shelf), manufacturer="WD-40 Company", category="Hardware", photo_label="WD-40 (shelf)")
item("Gumout Carb and Choke Cleaner", L(g_shelf), manufacturer="Gumout", category="Automotive")
item("Gumout Jet Spray Carb and Choke Cleaner", L(g_shelf), manufacturer="Gumout", category="Automotive")

# Consumables for the machines (brand doesn't matter, designation does)
castrol = item("Castrol GTX 10W-30 5L", L(g_rack), qty=1, category="Automotive")
put(f"products/{castrol['product_id']}/parts", json={"content_quantity": 5, "content_unit": "L"})
item("NGK BPR6ES spark plug", L(g_shelf), qty=2, category="Automotive")
item("Oil filter Kawasaki 49065-0736", L(g_rack), qty=0, category="Automotive", model_number="49065-0736")
item("Stabiliser for fuel", L(shed), category="Garden")

# Equipment: zero-turn mower + generator
mower = item("Zero-turn mower Z-Master", L(garage), manufacturer="Toro", model_number="74914", category="Garden", purchase_price="5999.00", purchase_date="2022-04-15")
put(f"items/{mower['id']}/equipment", json={"equipment_type": "Ride-on mower", "meter": "hours", "current_hours": 212, "engine_manufacturer": "Kawasaki", "engine_model": "FR691V", "fuel_type": "Petrol"})
gen = item("Inverter generator EU22i", L(garage), manufacturer="Honda", model_number="EU22i", category="Garden")
put(f"items/{gen['id']}/equipment", json={"equipment_type": "Generator", "meter": "hours", "current_hours": 87, "fuel_type": "Petrol"})

oil30 = post("parts/standards", json={"name": "Engine oil SAE 10W-30", "specs": {"Viscosity": "10W-30"}, "unit": "L"})
plug = post("parts/standards", json={"name": "Spark plug", "specs": {}})
kfilter = post("parts/products", json={"name": "Kawasaki oil filter", "part_number": "49065-0736", "manufacturer": "Kawasaki"})
blade = post("parts/products", json={"name": "48 in Hi-Lift blade", "part_number": "112-9759", "manufacturer": "Toro"})
airf = post("parts/products", json={"name": "Air filter element", "part_number": "11013-7049", "manufacturer": "Kawasaki"})

def task(eq, title, **kw):
    return post(f"items/{eq['id']}/maintenance-tasks", json={"title": title, **kw})

t_oil = task(mower, "Change engine oil", interval_hours=100, interval_days=365)
t_filter = task(mower, "Replace oil filter", interval_hours=200)
t_blades = task(mower, "Sharpen mower blades", interval_hours=25)
t_air = task(mower, "Replace air filter", interval_hours=300)
t_plug = task(mower, "Replace spark plugs", interval_hours=300)
task(mower, "Grease deck spindles", interval_hours=50)
patch(f"maintenance-tasks/{t_blades['id']}", json={"next_due_hours": 205})   # overdue
patch(f"maintenance-tasks/{t_oil['id']}", json={"next_due_hours": 220})      # due soon
put(f"maintenance-tasks/{t_oil['id']}/parts", json={"parts": [{"product_id": oil30["id"], "quantity": 2.1, "unit": "L"}]})
put(f"maintenance-tasks/{t_filter['id']}/parts", json={"parts": [{"product_id": kfilter["id"], "quantity": 1}]})
put(f"maintenance-tasks/{t_plug['id']}/parts", json={"parts": [{"product_id": plug["id"], "quantity": 2}]})
put(f"maintenance-tasks/{t_air['id']}/parts", json={"parts": [{"product_id": airf["id"], "quantity": 1}]})
put(f"maintenance-tasks/{t_blades['id']}/parts", json={"parts": [{"product_id": blade["id"], "quantity": 3}]})
for p, role, q, u in ((oil30, "Engine oil", 2.1, "L"), (kfilter, "Oil filter", 1, None), (plug, "Spark plug", 2, None),
                      (blade, "Mower blade", 3, None), (airf, "Air filter", 1, None)):
    post(f"products/{mower['product_id']}/fitments", json={"part_product_id": p["id"], "role": role, "quantity": q, "unit": u})
g_oil = task(gen, "Change engine oil", interval_hours=100, interval_days=180)
patch(f"maintenance-tasks/{g_oil['id']}", json={"next_due_hours": 90})
put(f"maintenance-tasks/{g_oil['id']}/parts", json={"parts": [{"product_id": oil30["id"], "quantity": 0.4, "unit": "L"}]})
task(gen, "Clean spark arrester", interval_hours=100)

# Two manuals, ranked (engine manual wins engine jobs)
def pdf(lines):
    buf = io.BytesIO(); cv = canvas.Canvas(buf); y = 800
    for ln in lines: cv.drawString(60, y, ln); y -= 20
    cv.showPage(); cv.save(); return buf.getvalue()
op = upload(pdf(["Toro Z-Master Operator's Manual", "Maintenance schedule"]), "Toro Z-Master operator manual.pdf", kind="manual", item_id=mower["id"], recognise="false")
eng = upload(pdf(["Kawasaki FR691V Owner's Manual"]), "Kawasaki FR691V engine manual.pdf", kind="manual", item_id=mower["id"], recognise="false")
put(f"equipment/{mower['id']}/manuals", json={"order": [{"attachment_id": op["id"], "role": "operator"}, {"attachment_id": eng["id"], "role": "engine"}]})

# Shopping list
post("shopping-list", json={"name": "Trimmer line 0.095 in", "manufacturer": "Oregon", "model_number": "22-595", "url": "https://example.com/trimmer-line", "quantity": 1})
pt = get("items", params={"q": "Paper towels"})
pt = (pt["items"] if isinstance(pt, dict) else pt)[0]
post("shopping-list", json={"product_id": pt["product_id"], "quantity": 3})

# A loan
ladder = item("Extension ladder", L(garage), category="Tools")
post("items/bulk-lend", json={"item_ids": [ladder["id"]], "borrower": "Jordan (next door)", "expected_return": "2026-11-12"})
ids = {"mower": mower["id"], "tote": tote7["id"], "drill": drill["id"]}
json.dump(ids, open(sys.argv[1], "w")) if len(sys.argv) > 1 else None
print("seeded", ids)
