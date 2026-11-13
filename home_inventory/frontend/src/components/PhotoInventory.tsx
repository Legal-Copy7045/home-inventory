import { useState } from "react";
import { Link } from "react-router-dom";
import { Box, ChevronDown, ChevronUp, Layers, MapPin, Plus, Trash2 } from "lucide-react";
import { api, url, type Item, type Scan } from "../api";
import { PhotoViewer } from "./PhotoViewer";
import { Sheet, Stepper, useAction } from "./ui";

/** What was photographed: a container's contents, or a shelf / location. */
export interface ScanTarget {
  kind: "container" | "location";
  id: number;
  name: string;
  /** What it already holds directly (to offer "no longer here"). */
  items: Item[];
}

type Row = {
  key: number;
  action: "add" | "increase" | "skip";
  name: string;
  category: string;
  manufacturer: string;
  model_number: string;
  notes: string;
  position: string | null;
  quantity: number;
  confidence: number | null;
  existing_item_id: number | null;
  match: string | null;
  open: boolean;
  box: number[] | null;
  /** Save the cut-out as the item's photo (untick when the AI's box is off). */
  use_crop: boolean;
  kind: "item" | "container";
  container_type: string;
  unit: number | null;
  shelf: number | null;
  /** "Put on" chosen by hand ("loc:12" / "new:u0s2"); otherwise from the layout. */
  place: string | null;
};

type Choice = number | "new";
type UnitPlan = { name: string; choice: Choice; shelves: { name: string; choice: Choice }[] };
type Place = NonNullable<Scan["places"]>[number];

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const sameName = (a: string, b: string) => norm(a) === norm(b) || norm(a).replace(/^the /, "") === norm(b).replace(/^the /, "");

/**
 * Match the AI's racks and shelves to places that already exist under the
 * photographed location; anything without a match will be created.  A
 * single rack is taken to be the photographed place itself (so its shelves go
 * straight under it).
 */
function initialLayout(scan: Scan, areaId: number): UnitPlan[] {
  const places = scan.places || [];
  const under = (pid: number) => places.filter((p) => p.parent_id === pid);
  const units = scan.units || [];
  return units.map((u) => {
    const existing = under(areaId).find((p) => sameName(p.name, u.name));
    const choice: Choice = existing ? existing.id : units.length === 1 ? areaId : "new";
    return {
      name: u.name,
      choice,
      shelves: u.shelves.map((sh) => {
        const ex = typeof choice === "number" ? under(choice).find((p) => sameName(p.name, sh)) : undefined;
        return { name: sh, choice: ex ? ex.id : "new" };
      }),
    };
  });
}

/** Just this entry's part of the photo (CSS crop of the box). */
function CropPreview({ src, box }: { src: string; box: number[] }) {
  const [x, y, w, h] = box;
  const pos = (v: number, size: number) => (size >= 1 ? 0 : (v / (1 - size)) * 100);
  return (
    <div className="crop-preview" aria-hidden="true"
      style={{ backgroundImage: `url("${src}")`, backgroundSize: `${100 / w}% ${100 / h}%`, backgroundPosition: `${pos(x, w)}% ${pos(y, h)}%` }} />
  );
}

const conf = (c: number | null) => (c === null ? "unknown" : c >= 0.8 ? "high" : c >= 0.5 ? "medium" : "low");

/**
 * Review an AI photo inventory: everything the AI saw in one photo, each
 * fully editable (name, quantity, category, maker, model, where on the
 * shelf), removable, plus anything it missed.  Nothing is saved until
 * "Confirm inventory".
 */
export function ScanReview(props: { scan: Scan; target: ScanTarget; onClose: () => void }) {
  // The sheet opens while the photo is still being analysed; start the
  // editable list afresh when the result arrives.
  return <Review key={`${props.scan.id}:${props.scan.status}`} {...props} />;
}

function Review({ scan, target, onClose }: { scan: Scan; target: ScanTarget; onClose: () => void }) {
  const [rows, setRows] = useState<Row[]>(() =>
    scan.suggestions.map((s, i) => ({
      key: i,
      action: s.proposed_action,
      name: s.name,
      category: s.category || "",
      manufacturer: s.manufacturer || "",
      model_number: s.model_number || "",
      notes: s.position || "",
      position: s.position || null,
      quantity: s.quantity || 1,
      confidence: s.confidence,
      existing_item_id: s.match?.item_id || null,
      match: s.match ? `${s.match.name} (have ${s.match.quantity})` : null,
      open: false,
      box: s.box || null,
      use_crop: true,
      kind: s.kind || "item",
      container_type: s.container_type || "",
      unit: s.unit ?? null,
      shelf: s.shelf ?? null,
      place: null,
    })),
  );
  const area = target.kind === "location" ? target.id : null;
  const [layout, setLayout] = useState<UnitPlan[]>(() => (area !== null ? initialLayout(scan, area) : []));
  const places: Place[] = scan.places || [];
  // Things already recorded here that the photo didn't show.
  const [unseen, setUnseen] = useState<Record<number, "keep" | "missing" | "remove">>({});
  const { busy, run } = useAction();
  const where = target.kind === "container" ? `in ${target.name}` : `on ${target.name}`;
  const addLink = target.kind === "container" ? `/add?container_id=${target.id}` : `/add?location_id=${target.id}`;

  if (scan.status === "analysing" || scan.status === "pending") {
    return (
      <Sheet title="Analysing photo…" onClose={onClose}>
        <div className="center"><div className="spinner" /></div>
        <p className="muted" style={{ textAlign: "center" }}>Looking for items {where}. This can take up to a minute.</p>
      </Sheet>
    );
  }
  if (scan.status === "failed") {
    return (
      <Sheet title="Photo inventory" onClose={onClose}>
        <div className="error">{scan.error}</div>
        <p>The photo was kept. You can retry or add items manually.</p>
        <div className="row">
          <button className="btn primary" onClick={() => run(() => api.post(`scans/${scan.id}/retry`)).then(onClose)} disabled={busy}>Retry</button>
          <Link className="btn" to={addLink}>Add manually</Link>
        </div>
      </Sheet>
    );
  }
  const matchedIds = new Set(rows.filter((r) => r.existing_item_id).map((r) => r.existing_item_id));
  const notSeen = target.items.filter((i) => !matchedIds.has(i.id));
  const upd = (k: number, patch: Partial<Row>) => setRows((all) => all.map((r) => (r.key === k ? { ...r, ...patch } : r)));
  const kept = rows.filter((r) => r.action !== "skip").length;
  const placeName = (id: number) => places.find((p) => p.id === id)?.name || target.name;

  // "Put on" tokens: an existing place ("loc:<id>") or one to create ("new:<key>").
  const tokenFor = (unit: number | null, shelf: number | null): string => {
    if (area === null || unit === null || !layout[unit]) return `loc:${area}`;
    const u = layout[unit];
    if (shelf !== null && u.shelves[shelf]) {
      const sh = u.shelves[shelf];
      return sh.choice === "new" ? `new:u${unit}s${shelf}` : `loc:${sh.choice}`;
    }
    return u.choice === "new" ? `new:u${unit}` : `loc:${u.choice}`;
  };
  const placeOf = (r: Row) => r.place ?? tokenFor(r.unit, r.shelf);
  const newPlaces: { key: string; label: string }[] = [];
  layout.forEach((u, i) => {
    const unitLabel = u.choice === "new" ? u.name : placeName(u.choice);
    if (u.choice === "new") newPlaces.push({ key: `u${i}`, label: u.name });
    u.shelves.forEach((sh, j) => sh.choice === "new" && newPlaces.push({ key: `u${i}s${j}`, label: `${unitLabel} › ${sh.name}` }));
  });
  const setUnit = (i: number, patch: Partial<UnitPlan>) => setLayout((all) => all.map((u, k) => (k === i ? { ...u, ...patch } : u)));
  const setShelf = (i: number, j: number, patch: Partial<UnitPlan["shelves"][number]>) =>
    setLayout((all) => all.map((u, k) => (k === i ? { ...u, shelves: u.shelves.map((sh, m) => (m === j ? { ...sh, ...patch } : sh)) } : u)));
  const placeOptions = area === null ? [] : [
    { value: `loc:${area}`, label: `${target.name} (the whole area)` },
    ...places.filter((p) => p.id !== area).map((p) => ({ value: `loc:${p.id}`, label: p.path })),
    ...newPlaces.map((n) => ({ value: `new:${n.key}`, label: `New: ${n.label}` })),
  ];

  /** The racks/shelves to create: the new ones something is actually put on. */
  const newLocations = (used: Set<string>) => {
    const need = new Set<string>();
    used.forEach((t) => {
      if (!t.startsWith("new:")) return;
      const key = t.slice(4);
      need.add(key);
      const m = /^u(\d+)s\d+$/.exec(key);
      if (m && layout[Number(m[1])]?.choice === "new") need.add(`u${m[1]}`);
    });
    const out: Record<string, unknown>[] = [];
    layout.forEach((u, i) => need.has(`u${i}`) && out.push({ key: `u${i}`, name: u.name.trim() || `Unit ${i + 1}`, kind: "rack", parent_id: area }));
    layout.forEach((u, i) => u.shelves.forEach((sh, j) => {
      if (!need.has(`u${i}s${j}`)) return;
      out.push({ key: `u${i}s${j}`, name: sh.name.trim() || `Shelf ${j + 1}`, kind: "shelf",
        ...(u.choice === "new" ? { parent_key: `u${i}` } : { parent_id: u.choice }) });
    }));
    return out;
  };

  return (
    <Sheet title={`Review: ${target.name}`} onClose={onClose}>
      {scan.photo && <PhotoViewer photos={[scan.photo]} />}
      <p className="muted">
        The AI found {rows.length} thing{rows.length === 1 ? "" : "s"}{rows.some((r) => r.kind === "container") ? ", including labelled boxes (saved as containers you can open and inventory later)" : ""}. Check
        each one — tap <b>Details</b> for category, maker, model and type. Nothing is saved until you confirm.
      </p>
      <div className="stack">
        {layout.length > 0 && (
          <div className="card" style={{ marginBottom: 0 }}>
            <h3 className="row" style={{ gap: 6 }}><Layers size={18} /> Layout</h3>
            <p className="muted" style={{ marginTop: 0 }}>What the AI saw, left to right and top down. Match each to a place you already have, or keep <b>New</b> to create it. Only places something goes on are created.</p>
            {layout.map((u, i) => {
              const under = (pid: number) => places.filter((p) => p.parent_id === pid);
              return (
                <div key={i} className="layout-unit">
                  <div className="row" style={{ gap: 6 }}>
                    <select value={String(u.choice)} aria-label={`Where is ${u.name}`} style={u.choice === "new" ? { width: "6.5rem", flex: "0 0 6.5rem" } : { width: "auto", flex: 1 }}
                      onChange={(e) => setUnit(i, { choice: e.target.value === "new" ? "new" : Number(e.target.value) })}>
                      <option value="new">{u.choice === "new" ? "New" : `New: ${u.name}`}</option>
                      {area !== null && <option value={area}>This place ({target.name})</option>}
                      {area !== null && under(area).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    {u.choice === "new" && <input value={u.name} onChange={(e) => setUnit(i, { name: e.target.value })} aria-label="Name for the new rack" style={{ flex: 1, minWidth: 0, fontWeight: 600 }} />}
                  </div>
                  {u.shelves.map((sh, j) => (
                    <div key={j} className="row layout-shelf" style={{ gap: 6 }}>
                      <span className="muted">↳</span>
                      <select value={String(sh.choice)} aria-label={`Where is ${u.name} ${sh.name}`} style={sh.choice === "new" ? { width: "6.5rem", flex: "0 0 6.5rem" } : { width: "auto", flex: 1 }}
                        onChange={(e) => setShelf(i, j, { choice: e.target.value === "new" ? "new" : Number(e.target.value) })}>
                        <option value="new">{sh.choice === "new" ? "New" : `New: ${sh.name}`}</option>
                        {typeof u.choice === "number" && under(u.choice).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>
                      {sh.choice === "new" && <input value={sh.name} onChange={(e) => setShelf(i, j, { name: e.target.value })} aria-label="Name for the new shelf" style={{ flex: 1, minWidth: 0 }} />}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
        {rows.map((r) => (
          <div key={r.key} className="card" style={{ marginBottom: 0, opacity: r.action === "skip" ? 0.55 : 1 }}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              {r.box && scan.photo && (
                <label className="crop-pick" title="Use this cut-out as the item's photo">
                  <span style={{ opacity: r.use_crop ? 1 : 0.35 }}><CropPreview src={url(scan.photo.url)} box={r.box} /></span>
                  <span className="crop-pick-label">
                    <input type="checkbox" checked={r.use_crop} onChange={(e) => upd(r.key, { use_crop: e.target.checked })} /> Photo
                  </span>
                </label>
              )}
              <input value={r.name} onChange={(e) => upd(r.key, { name: e.target.value })} style={{ fontWeight: 600 }} aria-label="Name" placeholder="Name" />
              <button
                className="iconbtn"
                onClick={() => upd(r.key, { action: r.action === "skip" ? (r.existing_item_id ? "increase" : "add") : "skip" })}
                aria-label={r.action === "skip" ? "Include" : "Remove suggestion"}
              >
                {r.action === "skip" ? <Plus size={18} /> : <Trash2 size={18} />}
              </button>
            </div>
            <div className="row wrap" style={{ marginTop: 8 }}>
              {r.kind === "container"
                ? <span className="badge blue"><Box size={12} /> Box{r.container_type ? ` · ${r.container_type}` : ""}</span>
                : <Stepper value={r.quantity} min={1} onChange={(n) => upd(r.key, { quantity: n })} />}
              {r.confidence !== null && <span className={`badge conf-${conf(r.confidence)}`}>{conf(r.confidence)} confidence</span>}
              {r.position && <span className="badge"><MapPin size={12} /> {r.position}</span>}
              {r.match && (
                <select value={r.action} onChange={(e) => upd(r.key, { action: e.target.value as Row["action"] })} style={{ width: "auto" }} aria-label="What to do">
                  <option value="increase">Update existing quantity</option>
                  <option value="add">Add as new item</option>
                  <option value="skip">Already recorded — skip</option>
                </select>
              )}
              {r.action !== "increase" && (
                <button className="btn small ghost" onClick={() => upd(r.key, { open: !r.open })} aria-expanded={r.open}>
                  Details {r.open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
              )}
            </div>
            {r.match && <div className="muted" style={{ fontSize: ".85rem", marginTop: 6 }}>Matches: {r.match}</div>}
            {r.kind === "container" && scan.suggestions[r.key]?.container_match && (
              <div className="muted" style={{ fontSize: ".85rem", marginTop: 6 }}>
                Already recorded as a box here ({scan.suggestions[r.key]!.container_match!.name}) — skipped unless you add it again.
              </div>
            )}
            {area !== null && r.action === "add" && (
              <label className="row wrap" style={{ gap: 6, marginTop: 8, fontSize: ".9rem" }}>
                <MapPin size={14} /> Put on
                <select value={placeOf(r)} onChange={(e) => upd(r.key, { place: e.target.value })} style={{ width: "auto", flex: 1 }} aria-label={`Put ${r.name} on`}>
                  {placeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            )}
            {r.open && r.action !== "increase" && (
              <div className="grid two" style={{ marginTop: 10 }}>
                <label>Type
                  <select value={r.kind} onChange={(e) => upd(r.key, { kind: e.target.value as Row["kind"] })}>
                    <option value="item">Item</option>
                    <option value="container">Box / bin (holds other things)</option>
                  </select>
                </label>
                {r.kind === "container" && <label>Box type<input value={r.container_type} onChange={(e) => upd(r.key, { container_type: e.target.value })} placeholder="Clear plastic tote" /></label>}
                <label>Category<input value={r.category} onChange={(e) => upd(r.key, { category: e.target.value })} /></label>
                <label>Maker / brand<input value={r.manufacturer} onChange={(e) => upd(r.key, { manufacturer: e.target.value })} /></label>
                <label>Model number<input value={r.model_number} onChange={(e) => upd(r.key, { model_number: e.target.value })} /></label>
                <label>Notes (e.g. which shelf)<input value={r.notes} onChange={(e) => upd(r.key, { notes: e.target.value })} /></label>
              </div>
            )}
          </div>
        ))}
        <button
          className="btn"
          onClick={() =>
            setRows([...rows, { key: Date.now(), action: "add", name: "", category: "", manufacturer: "", model_number: "", notes: "", position: null, quantity: 1, confidence: null, existing_item_id: null, match: null, open: true, box: null, use_crop: true, kind: "item", container_type: "", unit: null, shelf: null, place: null }])
          }
        >
          <Plus size={18} /> Add something it missed
        </button>
        {notSeen.length > 0 && (
          <div className="card" style={{ marginBottom: 0 }}>
            <h3>Not seen in this photo</h3>
            <p className="muted">Already recorded {where}, but the AI didn't see them. They may just be hidden.</p>
            {notSeen.map((i) => (
              <div key={i.id} className="row between wrap" style={{ padding: "6px 0", gap: 6 }}>
                <span>{i.quantity} × {i.display_name}</span>
                <select value={unseen[i.id] || "keep"} onChange={(e) => setUnseen({ ...unseen, [i.id]: e.target.value as "keep" | "missing" | "remove" })} style={{ width: "auto" }} aria-label={`What to do with ${i.display_name}`}>
                  <option value="keep">Still here</option>
                  <option value="missing">Mark missing</option>
                  <option value="remove">Gone — delete</option>
                </select>
              </div>
            ))}
          </div>
        )}
        <button
          className="btn primary block big"
          disabled={busy}
          onClick={async () => {
            const chosen = rows.filter((r) => r.name.trim() || r.action === "increase");
            const used = new Set(chosen.filter((r) => r.action === "add").map(placeOf));
            const items = chosen
              .map((r) => ({
                action: r.action,
                existing_item_id: r.action === "increase" ? r.existing_item_id : null,
                name: r.name.trim() || null,
                category: r.category.trim() || null,
                manufacturer: r.manufacturer.trim() || null,
                model_number: r.model_number.trim() || null,
                notes: r.notes.trim() || null,
                quantity: r.quantity,
                confidence: r.confidence,
                box: r.action === "add" && r.use_crop ? r.box : null,
                kind: r.kind,
                container_type: r.kind === "container" ? r.container_type.trim() || null : null,
                ...(area !== null && r.action === "add"
                  ? placeOf(r).startsWith("new:") ? { location_key: placeOf(r).slice(4) } : { location_id: Number(placeOf(r).slice(4)) }
                  : {}),
              }));
            const pick = (v: string) => Object.entries(unseen).filter(([, a]) => a === v).map(([id]) => Number(id));
            const res = await run(() => api.post<{ created: number[]; increased: number[]; removed: number[] }>(`scans/${scan.id}/confirm`, { items, new_locations: area !== null ? newLocations(used) : [], remove_item_ids: pick("remove"), missing_item_ids: pick("missing") }));
            if (res) onClose();
          }}
        >
          Confirm inventory{kept ? ` (${kept})` : ""}
        </button>
        <button className="btn ghost block" onClick={() => run(() => api.post(`scans/${scan.id}/discard`)).then(onClose)}>Discard suggestions</button>
      </div>
    </Sheet>
  );
}

/** Shown while a just-taken photo is being analysed. */
export function scanPlaceholder(kind: ScanTarget["kind"], id: number): Scan {
  return {
    id: 0,
    container_id: kind === "container" ? id : null,
    location_id: kind === "location" ? id : null,
    status: "analysing",
    provider: null,
    model: null,
    error: null,
    photo: null,
    suggestions: [],
    created_at: "",
  };
}

