import { Fragment, useEffect, useState, type ReactNode } from "react";
import { ArrowRightLeft, Boxes, CheckSquare, Handshake, Pencil, Square, Trash2, Undo2, X } from "lucide-react";
import { api, type Item } from "../api";
import { PlacementPicker } from "./PlacementPicker";
import { ErrorBox, ItemRow, Sheet, Spinner, Thumb, useAction, useApi, useMe, useToast } from "./ui";
import { CategorySelect } from "./CategorySelect";

const STATUSES: [string, string][] = [
  ["stored", "In storage"],
  ["in_use", "In use"],
  ["temporarily_relocated", "Temporarily moved"],
  ["missing", "Missing"],
];

const plural = (n: number) => `${n} item${n === 1 ? "" : "s"}`;

/**
 * An item list with a "Select" mode: tick items (or every item matching the
 * current filter), then move, edit, lend, add to a kit or delete them
 * together.  Deleting offers Undo.  With ``groupByPlace`` a heading shows
 * each time the place changes (for "sort by location").
 *
 * ``total`` / ``onLoadMore`` page through long lists; ``allIds`` fetches
 * every matching id for "Select all N" beyond the loaded page.
 */
export function SelectableItems({
  items,
  total,
  groupByPlace = false,
  onChanged,
  onLoadMore,
  allIds,
  resetKey,
  empty,
  bare = false,
  inPlace = false,
  renderRow,
}: {
  items: Item[];
  total?: number;
  groupByPlace?: boolean;
  onChanged: () => void;
  onLoadMore?: () => void;
  allIds?: () => Promise<number[]>;
  resetKey?: string;
  empty?: ReactNode;
  /** Every item is in the place being shown: rows say what it is, not where. */
  inPlace?: boolean;
  /** Inside an existing card: no card of its own. */
  bare?: boolean;
  /** Custom row when not selecting (default: the standard item row). */
  renderRow?: (item: Item) => ReactNode;
}) {
  const me = useMe();
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [sheet, setSheet] = useState<null | "move" | "edit" | "delete" | "lend" | "kit">(null);
  const [undo, setUndo] = useState<number[] | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();
  const count = total ?? items.length;
  const ids = [...chosen];
  const allChosen = count > 0 && ids.length >= count;

  // A different filter or sort starts a fresh selection.
  useEffect(() => { setChosen(new Set()); }, [resetKey]);

  const toggle = (id: number) =>
    setChosen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const stop = () => { setSelecting(false); setChosen(new Set()); setSheet(null); };
  const startSelecting = () => { setUndo(null); setSelecting(true); };
  const done = () => { stop(); onChanged(); };

  async function selectAll() {
    if (allChosen) return setChosen(new Set());
    if (allIds && count > items.length) {
      const got = await run(allIds);
      if (got) setChosen(new Set(got));
    } else setChosen(new Set(items.map((i) => i.id)));
  }

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
          <li className={`selectable ${on ? "on" : ""}`} onClick={() => toggle(i.id)} role="checkbox" aria-checked={on} aria-label={i.display_name} tabIndex={0}
            onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(i.id); } }}>
            {on ? <CheckSquare size={22} className="tick" /> : <Square size={22} className="muted" />}
            <Thumb src={i.photo?.thumb_url} />
            <div className="grow">
              <div className="t">{i.display_name}{i.quantity !== 1 ? ` × ${i.quantity}` : ""}</div>
              <div className="s">{place}</div>
            </div>
          </li>
        ) : renderRow ? renderRow(i) : (
          <ItemRow item={i} hidePlace={inPlace} />
        )}
      </Fragment>
    );
  });

  return (
    <>
      {me.can_edit && items.length > 0 && (
        <div className="row between" style={bare ? { padding: "8px 16px 0" } : { marginBottom: 8 }}>
          {selecting ? (
            <>
              <button className="btn small" disabled={busy} onClick={selectAll}>{allChosen ? "Select none" : `Select all ${count}`}</button>
              <button className="btn small ghost" onClick={stop}><X size={16} /> Cancel</button>
            </>
          ) : (
            <button className="btn small" onClick={startSelecting}><CheckSquare size={16} /> Select</button>
          )}
        </div>
      )}
      {bare ? (items.length ? <ul className="list">{rows}</ul> : empty) : <div className="card tight">{items.length ? <ul className="list">{rows}</ul> : empty}</div>}
      {onLoadMore && count > items.length && (
        <button className="btn block" style={{ marginTop: 8 }} onClick={onLoadMore}>Show more ({count - items.length} left)</button>
      )}

      {undo && !selecting && (
        <div className="selection-bar" role="status">
          <span>Deleted {plural(undo.length)}</span>
          <span className="spacer" />
          <button className="btn small" disabled={busy} onClick={async () => {
            if (await run(() => api.post("items/bulk-restore", { item_ids: undo }), `Restored ${plural(undo.length)}`)) { setUndo(null); onChanged(); }
          }}><Undo2 size={16} /> Undo</button>
          <button className="btn small ghost" onClick={() => setUndo(null)} aria-label="Dismiss"><X size={16} /></button>
        </div>
      )}
      {selecting && (
        <div className="selection-bar" role="toolbar" aria-label="Selected items">
          <b>{ids.length} selected</b>
          <span className="spacer" />
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("move")}><ArrowRightLeft size={16} /> Move</button>
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("edit")}><Pencil size={16} /> Edit</button>
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("lend")}><Handshake size={16} /> Lend</button>
          <button className="btn small" disabled={!ids.length || busy} onClick={() => setSheet("kit")}><Boxes size={16} /> Kit</button>
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
            // Where each one was, so Undo can put them back (only when every
            // chosen item is on screen, i.e. its place is known).
            const byId = new Map(items.map((i) => [i.id, i]));
            const origins = new Map<string, { container_id: number | null; location_id: number | null; ids: number[] }>();
            const known = ids.every((id) => byId.has(id) && !byId.get(id)!.home_placement);
            if (known) for (const id of ids) {
              const pl = byId.get(id)!.placement;
              const o = { container_id: pl.container?.id ?? null, location_id: pl.container ? null : pl.location?.id ?? null };
              const key = `${o.container_id}:${o.location_id}`;
              if (!origins.has(key)) origins.set(key, { ...o, ids: [] });
              origins.get(key)!.ids.push(id);
            }
            const moved = await run(() => api.post("items/bulk-move", { item_ids: ids, container_id: p.container_id, location_id: p.location_id }));
            if (!moved) return;
            toast(`Moved ${plural(ids.length)} to ${p.label}`, false, known ? async () => {
              for (const o of origins.values()) await api.post("items/bulk-move", { item_ids: o.ids, container_id: o.container_id, location_id: o.location_id });
              onChanged();
            } : undefined);
            done();
          }}
        />
      )}
      {sheet === "edit" && <BulkEditSheet ids={ids} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "lend" && <BulkLendSheet ids={ids} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "kit" && <BulkKitSheet ids={ids} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "delete" && (
        <Sheet title={`Delete ${plural(ids.length)}?`} onClose={() => setSheet(null)}>
          <p>They leave the inventory but stay in the history. You can undo straight away, or restore them later from <b>Recently deleted</b>.</p>
          <ul className="muted" style={{ maxHeight: 180, overflow: "auto" }}>
            {items.filter((i) => chosen.has(i.id)).slice(0, 50).map((i) => <li key={i.id}>{i.display_name}</li>)}
            {ids.length > Math.min(50, items.filter((i) => chosen.has(i.id)).length) && <li>…and others</li>}
          </ul>
          <button className="btn danger block" disabled={busy} onClick={async () => {
            const gone = ids;
            if (await run(() => api.post("items/bulk-delete", { item_ids: gone }), `Deleted ${plural(gone.length)}`)) { setUndo(gone); done(); }
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
    <Sheet title={`Edit ${plural(ids.length)}`} onClose={onClose}>
      <p className="muted">Fill in only what you want to change; blank fields are left as they are. Category and maker are shared by every item of the same product.</p>
      <div className="grid two">
        <label>Category<CategorySelect value={f.category} onChange={(category) => setF({ ...f, category })} emptyLabel="Unchanged" /></label>
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
      }}>Apply to {plural(ids.length)}</button>
    </Sheet>
  );
}

function BulkLendSheet({ ids, onClose, onDone }: { ids: number[]; onClose: () => void; onDone: () => void }) {
  const [borrower, setBorrower] = useState("");
  const [due, setDue] = useState("");
  const { busy, run } = useAction();
  return (
    <Sheet title={`Lend ${plural(ids.length)}`} onClose={onClose}>
      <label>Lent to<input autoFocus value={borrower} onChange={(e) => setBorrower(e.target.value)} placeholder="Name" /></label>
      <label>Expected back<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
      <p className="muted">Whole quantities are lent. If any item is already lent out, nothing is changed.</p>
      <button className="btn primary block" disabled={busy || !borrower.trim()} onClick={async () => {
        if (await run(() => api.post("items/bulk-lend", { item_ids: ids, borrower: borrower.trim(), expected_return: due || null }), `Lent ${plural(ids.length)} to ${borrower.trim()}`)) onDone();
      }}><Handshake size={18} /> Lend {plural(ids.length)}</button>
    </Sheet>
  );
}

function BulkKitSheet({ ids, onClose, onDone }: { ids: number[]; onClose: () => void; onDone: () => void }) {
  const kits = useApi(() => api.get<{ id: number; name: string; member_count?: number }[]>("kits"), []);
  const [name, setName] = useState("");
  const { busy, run } = useAction();
  const toast = useToast();
  const add = async (kitId: number, kitName: string) => {
    const r = await run(() => api.post<{ added: number[] }>(`kits/${kitId}/members/bulk`, { item_ids: ids }));
    if (r) {
      toast(r.added.length ? `Added ${plural(r.added.length)} to ${kitName}` : `Already in ${kitName}`);
      onDone();
    }
  };
  return (
    <Sheet title={`Add ${plural(ids.length)} to a kit`} onClose={onClose}>
      {kits.error && !kits.data ? <ErrorBox error={kits.error} retry={kits.reload} /> : kits.loading && !kits.data ? <Spinner /> : (
        <div className="list card tight">
          {(kits.data || []).map((k) => (
            <a key={k.id} href="#" onClick={(e) => { e.preventDefault(); void add(k.id, k.name); }}>
              <Boxes size={20} /><div className="grow"><div className="t">{k.name}</div></div>
            </a>
          ))}
          {!kits.data?.length && <div className="empty">No kits yet.</div>}
        </div>
      )}
      <div className="row" style={{ marginTop: 10 }}>
        <input placeholder="Or a new kit name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" disabled={busy || !name.trim()} onClick={async () => {
          const kit = await run(() => api.post<{ id: number; name: string }>("kits", { name: name.trim() }));
          if (kit) await add(kit.id, kit.name);
        }}>Create &amp; add</button>
      </div>
    </Sheet>
  );
}

/** Recently deleted items: tick and restore several at once. */
export function RecentlyDeleted() {
  const list = useApi(() => api.get<{ items: Item[] }>("deleted-items"), []);
  const me = useMe();
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const { busy, run } = useAction();
  const items = list.data?.items || [];
  return (
    <div className="page">
      <h1>Recently deleted</h1>
      <p className="muted">The last 200 deleted items. Restoring puts an item back where it was (or leaves it unplaced if that place was deleted too).</p>
      {list.error && !list.data ? <ErrorBox error={list.error} retry={list.reload} /> : list.loading && !list.data ? <Spinner /> : items.length ? (
        <>
          <ul className="list card tight">
            {items.map((i) => {
              const on = chosen.has(i.id);
              return (
                <li key={i.id} className={`selectable ${on ? "on" : ""}`} role="checkbox" aria-checked={on} aria-label={i.display_name} tabIndex={0}
                  onClick={() => me.can_edit && setChosen((s) => { const n = new Set(s); if (n.has(i.id)) n.delete(i.id); else n.add(i.id); return n; })}>
                  {me.can_edit && (on ? <CheckSquare size={22} className="tick" /> : <Square size={22} className="muted" />)}
                  <Thumb src={i.photo?.thumb_url} />
                  <div className="grow">
                    <div className="t">{i.display_name}</div>
                    <div className="s">{i.placement.path} · deleted {i.deleted_at ? new Date(i.deleted_at).toLocaleString() : ""}</div>
                  </div>
                </li>
              );
            })}
          </ul>
          {me.can_edit && (
            <div className="selection-bar">
              <b>{chosen.size} selected</b>
              <span className="spacer" />
              <button className="btn small" onClick={() => setChosen(chosen.size === items.length ? new Set() : new Set(items.map((i) => i.id)))}>
                {chosen.size === items.length ? "Select none" : "Select all"}
              </button>
              <button className="btn small primary" disabled={!chosen.size || busy} onClick={async () => {
                if (await run(() => api.post("items/bulk-restore", { item_ids: [...chosen] }), `Restored ${plural(chosen.size)}`)) { setChosen(new Set()); list.reload(); }
              }}><Undo2 size={16} /> Restore</button>
            </div>
          )}
        </>
      ) : <div className="card"><div className="empty">Nothing deleted recently.</div></div>}
    </div>
  );
}
