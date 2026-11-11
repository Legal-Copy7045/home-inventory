import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowRightLeft, Camera, FileText, Handshake, Home, Map as MapIcon, Minus, Pencil, Plus, Star, Trash2, Undo2 } from "lucide-react";
import { api, uploadPhoto, url, type HistoryEvent, type Item, type ItemStatus, STATUS_LABELS } from "../api";
import { ItemFields, applyLinkDetails, toPayload, type ItemFormValue } from "../components/ItemFields";
import { LinkImport } from "../components/LinkImport";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { ErrorBox, PathView, Section, Sheet, Spinner, StatusBadge, Stepper, Thumb, fmtDate, fmtWhen, money, useAction, useApi, useMe, useToast } from "../components/ui";

function toForm(it: Item): ItemFormValue {
  const p = it.product!;
  return {
    name: p.name,
    description: p.description || "",
    category: p.category || "",
    manufacturer: p.manufacturer || "",
    model_number: p.model_number || "",
    barcode: p.barcode || "",
    barcode_format: "",
    sku: p.sku || "",
    tags: p.tags || "",
    url: p.url || "",
    label: it.label || "",
    notes: it.notes || "",
    quantity: it.quantity,
    min_quantity: p.min_quantity === null ? "" : String(p.min_quantity),
    expires_on: it.expires_on || "",
    purchase_price: it.purchase_price || "",
    currency: it.currency || "",
    purchase_date: it.purchase_date || "",
    retailer: it.retailer || "",
    warranty_start: it.warranty_start || "",
    warranty_expires: it.warranty_expires || "",
    serial_number: it.serial_number || "",
  };
}

export default function ItemDetail() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const item = useApi(() => api.get<Item>(`items/${id}`), [id]);
  const hist = useApi(() => api.get<{ events: HistoryEvent[] }>("history", { entity_type: "item", entity_id: id, limit: 30 }), [id]);
  const [sheet, setSheet] = useState<null | "edit" | "move" | "lend" | "status" | "docs">(null);
  const { busy, run } = useAction();
  const toast = useToast();

  if (item.loading && !item.data) return <Spinner />;
  if (item.error || !item.data) return <div className="page"><ErrorBox error={item.error} /></div>;
  const it = item.data;
  const edit = me.can_edit && !it.deleted_at;
  const refresh = () => {
    item.reload();
    hist.reload();
  };
  const photos = (it.attachments || []).filter((a) => a.kind === "photo");
  const docs = (it.attachments || []).filter((a) => a.kind !== "photo");

  async function act<T>(fn: () => Promise<T>, msg: string) {
    const r = await run(fn, msg);
    if (r !== undefined) refresh();
    return r;
  }

  return (
    <div className="page">
      {it.deleted_at && (
        <div className="notice row between">
          <span>This item was deleted {fmtWhen(it.deleted_at)}.</span>
          {me.can_edit && <button className="btn small" onClick={() => act(() => api.post(`items/${it.id}/restore`), "Restored")}>Restore</button>}
        </div>
      )}
      <div className="grid two">
        <div>
          <Thumb src={it.photo?.url} size="lg" />
          {photos.length > 1 && (
            <div className="photos" style={{ marginTop: 8 }}>
              {photos.map((p) => (
                <a key={p.id} className="ph" href={url(p.url)} target="_blank" rel="noreferrer">
                  <img src={url(p.thumb_url || p.url)} alt="" />
                  {p.id === it.photo?.id ? <span className="badge blue star">Main</span> : edit ? (
                    <button className="iconbtn star" style={{ color: "#fff" }} onClick={(e) => { e.preventDefault(); act(() => api.patch(`items/${it.id}`, { primary_photo_id: p.id }), "Main photo set"); }} aria-label="Make main photo"><Star size={16} /></button>
                  ) : null}
                </a>
              ))}
            </div>
          )}
          {edit && (
            <label className="btn block" style={{ marginTop: 8 }}>
              <Camera size={18} /> Add photo
              <input type="file" accept="image/*" capture="environment" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) await act(() => uploadPhoto(f, { item_id: it.id }), "Photo added"); }} />
            </label>
          )}
        </div>
        <div>
          <h1 style={{ marginBottom: 4 }}>{it.display_name}</h1>
          <div className="row wrap" style={{ gap: 6, marginBottom: 8 }}>
            {it.category && <span className="badge">{it.category}</span>}
            <StatusBadge item={it} />
            {it.identification_status === "confirmed" && it.product?.ai_metadata ? <span className="badge">AI-assisted</span> : null}
          </div>
          <div className="card">
            <div className="row between">
              <div>
                <div className="muted" style={{ fontSize: ".85rem", fontWeight: 600 }}>Quantity here</div>
                <div style={{ fontSize: "1.8rem", fontWeight: 800 }}>{it.quantity}</div>
              </div>
              {edit && (
                <div className="row">
                  <button className="btn" disabled={busy || it.quantity === 0} onClick={() => act(() => api.post(`items/${it.id}/adjust`, { delta: -1 }), "Used one")} aria-label="Use one"><Minus size={18} /> Use</button>
                  <button className="btn" disabled={busy} onClick={() => act(() => api.post(`items/${it.id}/adjust`, { delta: 1 }), "Added one")} aria-label="Add one"><Plus size={18} /></button>
                </div>
              )}
            </div>
            {it.min_quantity !== null && (
              <p className="muted" style={{ margin: "8px 0 0" }}>
                Low-stock alert below {it.min_quantity} in total{it.low_stock ? " — running low" : ""}.
              </p>
            )}
            <hr />
            <div className="muted" style={{ fontSize: ".85rem", fontWeight: 600 }}>{it.status === "stored" ? "Location" : "Current location"}</div>
            <PathView placement={it.placement} />
            {it.home_placement && (
              <>
                <div className="muted" style={{ fontSize: ".85rem", fontWeight: 600, marginTop: 8 }}>Normally kept in</div>
                <PathView placement={it.home_placement} />
              </>
            )}
            {it.loan && (
              <p style={{ marginTop: 10, marginBottom: 0 }}>
                Lent to <b>{it.loan.borrower}</b> on {fmtDate(it.loan.loaned_at)}
                {it.loan.expected_return && <> · due {fmtDate(it.loan.expected_return)}</>}
                {it.loan.overdue && <span className="badge danger" style={{ marginLeft: 6 }}>Overdue</span>}
              </p>
            )}
          </div>
          {edit && (
            <div className="quick" style={{ marginBottom: 14 }}>
              <button onClick={() => setSheet("move")}><span className="ico"><ArrowRightLeft size={20} /></span>Move</button>
              {it.loan ? (
                <button onClick={() => act(() => api.post(`loans/${it.loan!.id}/return`, {}), "Marked as returned")}><span className="ico"><Undo2 size={20} /></span>Returned</button>
              ) : (
                <button onClick={() => setSheet("lend")}><span className="ico"><Handshake size={20} /></span>Lend</button>
              )}
              {it.home_placement ? (
                <button onClick={() => act(() => api.post(`items/${it.id}/return`), "Back in its place")}><span className="ico"><Home size={20} /></span>Put back</button>
              ) : (
                <button onClick={() => setSheet("status")}><span className="ico"><Star size={20} /></span>Status</button>
              )}
              <button onClick={() => setSheet("edit")}><span className="ico"><Pencil size={20} /></span>Edit</button>
            </div>
          )}
        </div>
      </div>

      <Section title="Details">
        <dl className="kv">
          {it.manufacturer && (<><dt>Manufacturer</dt><dd>{it.manufacturer}</dd></>)}
          {it.model_number && (<><dt>Model</dt><dd>{it.model_number}</dd></>)}
          {it.serial_number && (<><dt>Serial</dt><dd className="mono">{it.serial_number}</dd></>)}
          {it.barcode && (<><dt>Barcode</dt><dd className="mono">{it.barcode}</dd></>)}
          {it.product_url && (<><dt>Product page</dt><dd><a href={it.product_url} target="_blank" rel="noopener noreferrer">{(() => { try { return new URL(it.product_url).hostname.replace(/^www\./, ""); } catch { return it.product_url; } })()} ↗</a></dd></>)}
          {it.expires_on && (<><dt>Expires</dt><dd>{fmtDate(it.expires_on)}{it.expired ? " (expired)" : ""}</dd></>)}
          {it.purchase_price && (<><dt>Price</dt><dd>{money(it.purchase_price, it.currency || me.app.currency)}</dd></>)}
          {it.purchase_date && (<><dt>Bought</dt><dd>{fmtDate(it.purchase_date)}{it.retailer ? ` at ${it.retailer}` : ""}</dd></>)}
          {it.warranty_expires && (<><dt>Warranty</dt><dd>{it.warranty_start ? `${fmtDate(it.warranty_start)} – ` : "until "}{fmtDate(it.warranty_expires)} {it.warranty_active ? <span className="badge ok">active</span> : <span className="badge">expired</span>}</dd></>)}
          {it.product?.description && (<><dt>Description</dt><dd>{it.product.description}</dd></>)}
          {it.notes && (<><dt>Notes</dt><dd style={{ whiteSpace: "pre-line" }}>{it.notes}</dd></>)}
          <dt>Added</dt><dd>{fmtDate(it.created_at)}</dd>
        </dl>
      </Section>

      <Section title="Receipts & documents" action={edit && <button className="btn small" onClick={() => setSheet("docs")}><Plus size={16} /> Add</button>}>
        {docs.length ? (
          <div className="list">
            {docs.map((d) => (
              <li key={d.id}>
                <FileText size={20} />
                <a className="grow" href={url(d.url)} target="_blank" rel="noreferrer">
                  <div className="t">{d.original_name || d.kind}</div>
                  <div className="s">{d.kind} · {fmtDate(d.created_at)}</div>
                </a>
                {edit && <button className="iconbtn" onClick={() => window.confirm("Remove this document?") && act(() => api.del(`attachments/${d.id}`), "Removed")} aria-label="Remove"><Trash2 size={18} /></button>}
              </li>
            ))}
          </div>
        ) : (
          <p className="muted">No receipts, manuals or warranty documents yet.</p>
        )}
      </Section>

      {(it.siblings?.length || 0) > 0 && (
        <Section title={`Also own (${it.siblings!.reduce((a, s) => a + s.quantity, 0)} more)`} tight>
          <div className="list">
            {it.siblings!.map((s) => (
              <Link key={s.id} to={`/items/${s.id}`}>
                <div className="grow">
                  <div className="t">{s.label || it.name}</div>
                  <div className="s">{s.path} · {STATUS_LABELS[s.status as ItemStatus]}</div>
                </div>
                <div className="qty">×{s.quantity}</div>
              </Link>
            ))}
          </div>
        </Section>
      )}
      {edit && (
        <Link className="btn block" style={{ marginBottom: 14 }} to={`/add?product_id=${it.product_id}&item_id=${it.id}`}>
          <Plus size={18} /> Add another one somewhere else
        </Link>
      )}

      {it.maps && it.maps.length > 0 && (
        <Section title="On the map">
          {it.maps.map((m) => <Link key={m.id} className="btn" to={`/maps/${m.id}`}><MapIcon size={18} /> {m.title}</Link>)}
        </Section>
      )}

      <Section title="History" tight>
        <div className="list">
          {hist.data?.events.map((e) => (
            <li key={e.id}>
              <div className="grow">
                <div style={{ fontWeight: 500 }}>{e.summary}</div>
                <div className="s">{e.user || "System"} · {fmtWhen(e.ts)}</div>
              </div>
            </li>
          ))}
        </div>
      </Section>

      {edit && (
        <button className="btn danger block" onClick={async () => {
          const reason = window.prompt("Delete this item? It can be restored from the Trash. Optional reason:", "");
          if (reason === null) return;
          try {
            await api.del(`items/${it.id}`, { reason });
            toast("Deleted — restore it from Trash in Settings if needed");
            nav(-1);
          } catch (err) {
            toast((err as Error).message, true);
          }
        }}>
          <Trash2 size={18} /> Delete
        </button>
      )}

      {sheet === "edit" && <EditSheet item={it} onClose={() => setSheet(null)} onSaved={() => { setSheet(null); refresh(); }} />}
      {sheet === "move" && <MoveSheet item={it} onClose={() => setSheet(null)} onDone={(newId) => { setSheet(null); if (newId !== it.id) nav(`/items/${newId}`); else refresh(); }} />}
      {sheet === "lend" && <LendSheet item={it} onClose={() => setSheet(null)} onDone={(newId) => { setSheet(null); if (newId !== it.id) nav(`/items/${newId}`); else refresh(); }} />}
      {sheet === "status" && <StatusSheet item={it} onClose={() => setSheet(null)} onDone={() => { setSheet(null); refresh(); }} />}
      {sheet === "docs" && <DocSheet item={it} onClose={() => setSheet(null)} onDone={() => { setSheet(null); refresh(); }} />}
    </div>
  );
}

function EditSheet({ item, onClose, onSaved }: { item: Item; onClose: () => void; onSaved: () => void }) {
  const [v, setV] = useState(toForm(item));
  const { busy, run } = useAction();
  return (
    <Sheet title="Edit item" onClose={onClose}>
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Fill from a web link</h3>
        <p className="muted" style={{ marginTop: 0 }}>Only empty fields are filled; nothing you have entered is replaced.</p>
        <LinkImport initialUrl={v.url} downloadImage={false} buttonLabel="Fill" onImported={(r) => setV((cur) => applyLinkDetails(cur, r.details, false))} />
      </div>
      <form onSubmit={async (e) => { e.preventDefault(); if (await run(() => api.patch(`items/${item.id}`, { ...toPayload(v), identification_status: "confirmed" }), "Saved")) onSaved(); }}>
        <ItemFields value={v} onChange={setV} sharedCount={item.siblings?.length || 0} />
        <button className="btn primary block" style={{ marginTop: 14 }} disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

function MoveSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: (id: number) => void }) {
  const [target, setTarget] = useState<PlacementValue | null>(null);
  const [picking, setPicking] = useState(true);
  const [qty, setQty] = useState(item.quantity);
  const [temporary, setTemporary] = useState(false);
  const { busy, run } = useAction();
  return (
    <Sheet title={`Move ${item.name}`} onClose={onClose}>
      <div className="stack">
        <button className="btn block" style={{ justifyContent: "flex-start" }} onClick={() => setPicking(true)}>
          <ArrowRightLeft size={18} /> {target ? target.label : "Choose destination…"}
        </button>
        {item.quantity > 1 && (
          <label className="field">How many?<Stepper value={qty} min={1} onChange={(n) => setQty(Math.min(n, item.quantity))} /></label>
        )}
        <label className="check"><input type="checkbox" checked={temporary} onChange={(e) => setTemporary(e.target.checked)} /> Temporary move (remember where it normally lives)</label>
        <button className="btn primary block" disabled={!target || busy} onClick={async () => {
          const r = await run(() => api.post<Item>(`items/${item.id}/move`, { container_id: target!.container_id, location_id: target!.location_id, quantity: qty < item.quantity ? qty : null, temporary }), "Moved");
          if (r) onDone(r.id);
        }}>Move</button>
      </div>
      {picking && <PlacementPicker onClose={() => setPicking(false)} onPick={(p) => { setTarget(p); setPicking(false); }} />}
    </Sheet>
  );
}

function LendSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: (id: number) => void }) {
  const [borrower, setBorrower] = useState("");
  const [due, setDue] = useState("");
  const [notes, setNotes] = useState("");
  const [qty, setQty] = useState(item.quantity);
  const { busy, run } = useAction();
  return (
    <Sheet title={`Lend ${item.name}`} onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api.post<{ item: Item }>(`items/${item.id}/loan`, { borrower, expected_return: due || null, notes: notes || null, quantity: qty }), `Lent to ${borrower}`);
        if (r) onDone(r.item.id);
      }}>
        <label className="field">Lent to<input required autoFocus value={borrower} onChange={(e) => setBorrower(e.target.value)} placeholder="Name" /></label>
        {item.quantity > 1 && <label className="field">How many?<Stepper value={qty} min={1} onChange={(n) => setQty(Math.min(n, item.quantity))} /></label>}
        <label className="field">Expected back<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <label className="field">Notes<textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} /></label>
        <button className="btn primary block" disabled={busy}>Lend</button>
      </form>
    </Sheet>
  );
}

function StatusSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState(item.status_note || "");
  const { busy, run } = useAction();
  const options: ItemStatus[] = ["stored", "in_use", "temporarily_relocated", "awaiting_return", "missing"];
  return (
    <Sheet title="Status" onClose={onClose}>
      <label className="field" style={{ marginBottom: 10 }}>Note<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" /></label>
      <div className="stack">
        {options.map((s) => (
          <button key={s} className={`btn block ${item.status === s ? "primary" : ""}`} disabled={busy} onClick={async () => { if (await run(() => api.post(`items/${item.id}/status`, { status: s, note: note || null }), "Updated")) onDone(); }}>
            {STATUS_LABELS[s]}
          </button>
        ))}
      </div>
    </Sheet>
  );
}

function DocSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState("receipt");
  const { busy, run } = useAction();
  return (
    <Sheet title="Add document" onClose={onClose}>
      <div className="stack">
        <label className="field">Type
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="receipt">Receipt</option>
            <option value="warranty">Warranty</option>
            <option value="manual">Manual / documentation</option>
            <option value="document">Other document</option>
          </select>
        </label>
        <label className="btn primary block">
          {busy ? <div className="spinner" /> : <FileText size={18} />} Choose photo or PDF
          <input type="file" accept="image/*,application/pdf" hidden onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f && (await run(() => uploadPhoto(f, { item_id: item.id, kind }), "Added"))) onDone();
          }} />
        </label>
      </div>
    </Sheet>
  );
}
