import { Fragment, useState } from "react";
import { ArrowRightLeft, CheckSquare, Pencil, Square, Trash2, X } from "lucide-react";
import { api, type Item } from "../api";
import { PlacementPicker } from "./PlacementPicker";
import { ItemRow, Sheet, Thumb, useAction, useMe } from "./ui";

const STATUSES: [string, string][] = [
  ["stored", "In storage"],
  ["in_use", "In use"],
  ["temporarily_relocated", "Temporarily moved"],
  ["missing", "Missing"],
];

/**
 * An item list with a "Select" mode: tick several items, then move, edit or
 * delete them together.  With ``groupByPlace`` a heading is shown each time
 * the place changes (for "sort by location").
 */
export function SelectableItems({ items, groupByPlace = false, onChanged }: { items: Item[]; groupByPlace?: boolean; onChanged: () => void }) {
  const me = useMe();
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [sheet, setSheet] = useState<null | "move" | "edit" | "delete">(null);
  const { busy, run } = useAction();
  const visible = new Set(items.map((i) => i.id));
  const ids = [...chosen].filter((id) => visible.has(id));
  const all = items.length > 0 && ids.length === items.length;

  const toggle = (id: number) =>
    setChosen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const stop = () => { setSelecting(false); setChosen(new Set()); setSheet(null); };
  const done = () => { stop(); onChanged(); };
  const plural = (n: number) => `${n} item${n === 1 ? "" : "s"}`;

  let lastPlace = "";
  const rows = items.map((i) => {
    const place = i.placement.path;
    const head = groupByPlace && place !== lastPlace ? <li key={`h-${i.id}`} className="list-head">{place}</li> : null;
    lastPlace = place;
    const on = chosen.has(i.id);
    return (
      <Fragment key={i.id}>
        {head}
        {selecting ? (
          <li className={`selectable ${on ? "on" : ""}`} onClick={() => toggle(i.id)} role="checkbox" aria-checked={on} tabIndex={0}
            onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(i.id); } }}>
            {on ? <CheckSquare size={22} className="tick" /> : <Square size={22} className="muted" />}
            <Thumb src={i.photo?.thumb_url} />
            <div className="grow">
              <div className="t">{i.display_name}{i.quantity !== 1 ? ` × ${i.quantity}` : ""}</div>
              <div className="s">{place}</div>
            </div>
          </li>
        ) : (
          <ItemRow item={i} />
        )}
      </Fragment>
    );
  });

  return (
    <>
      {me.can_edit && items.length > 0 && (
        <div className="row between" style={{ marginBottom: 8 }}>
          {selecting ? (
            <>
              <button className="btn small" onClick={() => setChosen(all ? new Set() : new Set(items.map((i) => i.id)))}>
                {all ? "Select none" : `Select all ${items.length}`}
              </button>
              <button className="btn small ghost" onClick={stop}><X size={16} /> Cancel</button>
            </>
          ) : (
            <button className="btn small" onClick={() => setSelecting(true)}><CheckSquare size={16} /> Select</button>
          )}
        </div>
      )}
      <div className="card tight"><ul className="list">{rows}</ul></div>

      {selecting && (
        <div className="selection-bar" role="toolbar" aria-label="Selected items">
          <b>{ids.length} selected</b>
          <span className="spacer" />
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("move")}><ArrowRightLeft size={16} /> Move</button>
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("edit")}><Pencil size={16} /> Edit</button>
          <button className="btn small danger" disabled={!ids.length || busy} onClick={() => setSheet("delete")}><Trash2 size={16} /> Delete</button>
        </div>
      )}

      {sheet === "move" && (
        <PlacementPicker
          title={`Move ${plural(ids.length)} to…`}
          onClose={() => setSheet(null)}
          onPick={async (p) => {
            setSheet(null);
            if (p.container_id === null && p.location_id === null) return;
            if (await run(() => api.post("items/bulk-move", { item_ids: ids, container_id: p.container_id, location_id: p.location_id }), `Moved ${plural(ids.length)} to ${p.label}`)) done();
          }}
        />
      )}
      {sheet === "edit" && <BulkEditSheet ids={ids} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "delete" && (
        <Sheet title={`Delete ${plural(ids.length)}?`} onClose={() => setSheet(null)}>
          <p>They leave the inventory but stay in the history, and each one can be restored from its page.</p>
          <ul className="muted" style={{ maxHeight: 180, overflow: "auto" }}>
            {items.filter((i) => chosen.has(i.id)).map((i) => <li key={i.id}>{i.display_name}</li>)}
          </ul>
          <button className="btn danger block" disabled={busy} onClick={async () => {
            if (await run(() => api.post("items/bulk-delete", { item_ids: ids }), `Deleted ${plural(ids.length)}`)) done();
          }}><Trash2 size={18} /> Delete {plural(ids.length)}</button>
        </Sheet>
      )}
    </>
  );
}

function BulkEditSheet({ ids, onClose, onDone }: { ids: number[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ category: "", manufacturer: "", retailer: "", purchase_date: "", status: "" });
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]));
  return (
    <Sheet title={`Edit ${ids.length} item${ids.length === 1 ? "" : "s"}`} onClose={onClose}>
      <p className="muted">Fill in only what you want to change; blank fields are left as they are. Category and maker are shared by every item of the same product.</p>
      <div className="grid two">
        <label>Category<input value={f.category} onChange={set("category")} placeholder="Unchanged" /></label>
        <label>Maker / brand<input value={f.manufacturer} onChange={set("manufacturer")} placeholder="Unchanged" /></label>
        <label>Bought from<input value={f.retailer} onChange={set("retailer")} placeholder="Unchanged" /></label>
        <label>Purchase date<input type="date" value={f.purchase_date} onChange={set("purchase_date")} /></label>
        <label>Status
          <select value={f.status} onChange={set("status")}>
            <option value="">Unchanged</option>
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>
      <button className="btn primary block" style={{ marginTop: 12 }} disabled={busy || !Object.keys(body).length} onClick={async () => {
        if (await run(() => api.post("items/bulk-edit", { item_ids: ids, ...body }), "Saved")) onDone();
      }}>Apply to {ids.length} item{ids.length === 1 ? "" : "s"}</button>
    </Sheet>
  );
}
