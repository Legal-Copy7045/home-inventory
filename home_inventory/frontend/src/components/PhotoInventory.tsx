import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronUp, MapPin, Plus, Trash2 } from "lucide-react";
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
};

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
    })),
  );
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

  return (
    <Sheet title={`Review: ${target.name}`} onClose={onClose}>
      {scan.photo && <PhotoViewer photos={[scan.photo]} />}
      <p className="muted">
        The AI found {rows.length} kind{rows.length === 1 ? "" : "s"} of item. Check and edit each one — tap <b>Details</b> for category, maker, model and
        where it is. Nothing is saved until you confirm.
      </p>
      <div className="stack">
        {rows.map((r) => (
          <div key={r.key} className="card" style={{ marginBottom: 0, opacity: r.action === "skip" ? 0.55 : 1 }}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              {r.box && scan.photo && <CropPreview src={url(scan.photo.url)} box={r.box} />}
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
              <Stepper value={r.quantity} min={1} onChange={(n) => upd(r.key, { quantity: n })} />
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
            {r.open && r.action !== "increase" && (
              <div className="grid two" style={{ marginTop: 10 }}>
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
            setRows([...rows, { key: Date.now(), action: "add", name: "", category: "", manufacturer: "", model_number: "", notes: "", position: null, quantity: 1, confidence: null, existing_item_id: null, match: null, open: true, box: null }])
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
            const items = rows
              .filter((r) => r.name.trim() || r.action === "increase")
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
                box: r.action === "add" ? r.box : null,
              }));
            const pick = (v: string) => Object.entries(unseen).filter(([, a]) => a === v).map(([id]) => Number(id));
            const res = await run(() => api.post<{ created: number[]; increased: number[]; removed: number[] }>(`scans/${scan.id}/confirm`, { items, remove_item_ids: pick("remove"), missing_item_ids: pick("missing") }));
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

