import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowRightLeft, Camera, ChevronLeft, ChevronRight, Hand, FileText, Handshake, Home, Map as MapIcon, Minus, Pencil, Plus, Sparkles, Star, Trash2, Undo2 } from "lucide-react";
import { api, uploadPhoto, url, type HistoryEvent, type Item, type ItemStatus, STATUS_LABELS } from "../api";
import { neighbours } from "../browse";
import { PutAwayButtons, takeItem } from "../components/TakeAway";
import { ItemFields, applyLinkDetails, toPayload, type ItemFormValue } from "../components/ItemFields";
import { LinkImport } from "../components/LinkImport";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { CustomerItemSection } from "./Customers";
import { RelatedSection } from "./Kits";
import { RestockSection } from "./Shopping";
import { TagsSection } from "./Tags";
import { EquipmentSection } from "./Maintenance";
import { PartDetailsSection } from "../components/Parts";
import { DocumentsList } from "../components/Documents";
import { PhotoViewer } from "../components/PhotoViewer";
import { ErrorBox, PathView, Section, Sheet, Spinner, StatusBadge, Stepper, fmtDate, fmtWhen, money, useAction, useApi, useMe, useToast } from "../components/ui";

function toForm(it: Item): ItemFormValue {
  const p = it.product!;
  const s = it.needs_details ? it.capture?.suggestion : null;
  return {
    name: it.needs_details ? s?.name || "" : p.name,
    description: p.description || s?.description || "",
    category: p.category || s?.category || "",
    manufacturer: p.manufacturer || s?.manufacturer || "",
    model_number: p.model_number || s?.model_number || "",
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

// Maintenance is for equipment: only items in the "Equipment" category get the
// "Track maintenance" link.  Items already being tracked keep their section.
function isEquipmentCategory(it: Item): boolean {
  return (it.category || "").trim().toLowerCase() === "equipment";
}

/** Previous / next through the list the user last browsed (or the whole inventory by name). */
function ItemSteps({ id }: { id: number }) {
  const [n, setN] = useState<{ prev: number | null; next: number | null; pos: number; total: number } | null>(null);
  const nav = useNavigate();
  useEffect(() => {
    let live = true;
    neighbours(id).then((r) => live && setN(r)).catch(() => live && setN(null));
    return () => { live = false; };
  }, [id]);
  if (!n || n.total < 2 || !n.pos) return null;
  const go = (to: number | null) => to && nav(`/items/${to}`, { replace: true });
  return (
    <div className="row between item-steps">
      <button className="btn small ghost" disabled={!n.prev} onClick={() => go(n.prev)} aria-label="Previous item"><ChevronLeft size={18} /> Previous</button>
      <span className="muted small">{n.pos} of {n.total}</span>
      <button className="btn small ghost" disabled={!n.next} onClick={() => go(n.next)} aria-label="Next item">Next <ChevronRight size={18} /></button>
    </div>
  );
}

export default function ItemDetail() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const item = useApi(() => api.get<Item>(`items/${id}`), [id]);
  const hist = useApi(() => api.get<{ events: HistoryEvent[] }>("history", { entity_type: "item", entity_id: id, limit: 30 }), [id]);
  const [params] = useSearchParams();
  const [partOpen, setPartOpen] = useState(false);
  const [trackOpen, setTrackOpen] = useState(false);
  // Equipment = maintenance is tracked; only then does the Maintenance section show.
  const maint = useApi(() => api.get<{ profile: { managed?: boolean } | null; tasks: unknown[]; history: unknown[] }>(`items/${id}/maintenance`), [id]);
  // Managed equipment has its own pages; an item that was equipment once keeps its history here.
  const managed = !!maint.data?.profile && maint.data.profile.managed !== false;
  const [sheet, setSheet] = useState<null | "edit" | "move" | "lend" | "status" | "docs">(params.get("finish") ? "edit" : null);
  // Moving to another item reuses this page: close any sheet from the previous one.
  // ?do=move (e.g. from an NFC tag) opens that action straight away.
  const doAction = params.get("do");
  useEffect(() => { setSheet(params.get("finish") ? "edit" : doAction === "move" ? "move" : null); }, [id, params, doAction]);
  // Opening an empty optional section's form from the Add row.
  const [addReq, setAddReq] = useState({ related: 0, restock: 0, tag: 0 });
  const request = (k: keyof typeof addReq) => setAddReq((r) => ({ ...r, [k]: r[k] + 1 }));
  const { busy, run } = useAction();
  const toast = useToast();
  const [putPick, setPutPick] = useState(false);
  const [putPlace, setPutPlace] = useState<PlacementValue | null>(null);

  if (item.error && !item.data) return <ErrorBox error={item.error} retry={item.reload} />;
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

  // Quick changes say what happened with an Undo, rather than asking first.
  async function adjust(delta: number) {
    const r = await run(() => api.post(`items/${it.id}/adjust`, { delta }));
    if (r === undefined) return;
    refresh();
    toast(delta < 0 ? "Used one" : "Added one", false, async () => {
      await api.post(`items/${it.id}/adjust`, { delta: -delta, note: "Undo" });
      refresh();
    });
  }

  return (
    <div className="page">
      <ItemSteps id={it.id} />
      {putPick && <PlacementPicker title={`Put ${it.display_name} away in…`} onClose={() => setPutPick(false)} onPick={(p) => { setPutPick(false); setPutPlace(p); }} />}
      {putPlace && (
        <Sheet title={`Put away in ${putPlace.label}`} onClose={() => setPutPlace(null)}>
          <PutAwayButtons item={it} place={putPlace} onDone={(m) => { toast(m); setPutPlace(null); refresh(); }} />
        </Sheet>
      )}
      {it.needs_details && !it.deleted_at && (
        <div className="notice row between">
          <span>Added with Quick photos — details still to add{it.capture?.suggestion ? `. AI thinks it's: ${it.capture.suggestion.name}` : ""}.</span>
          {me.can_edit && <button className="btn small primary" onClick={() => setSheet("edit")}>Add details</button>}
        </div>
      )}
      {it.deleted_at && (
        <div className="notice row between">
          <span>This item was deleted {fmtWhen(it.deleted_at)}.</span>
          {me.can_edit && <button className="btn small" onClick={() => act(() => api.post(`items/${it.id}/restore`), "Restored")}>Restore</button>}
        </div>
      )}
      <div className="grid two">
        <div>
          {photos.length > 0 && <PhotoViewer photos={photos} mainId={it.photo?.id} onMakeMain={edit ? (id) => act(() => api.patch(`items/${it.id}`, { primary_photo_id: id }), "Main photo set") : undefined} />}
          {edit && (
            <label className={photos.length ? "btn block" : "btn block photo-empty"} style={{ marginTop: photos.length ? 8 : 0 }}>
              <Camera size={18} /> Add photo
              <input type="file" accept="image/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) await act(() => uploadPhoto(f, { item_id: it.id }), "Photo added"); }} />
            </label>
          )}
          {edit && me.app.ai.enabled && it.photo?.caption === "Cut from photo inventory" && (
            <button className="btn block ghost" style={{ marginTop: 6 }} onClick={async () => {
              if (!window.confirm("Make the cut-out photos again for everything from this photo inventory? The AI looks at the original photo once more. Photos you added or chose yourself are kept.")) return;
              const r = await run(() => api.post<{ fixed: number; whole_photo: number; unchanged: number }>(`items/${it.id}/redo-cutouts`));
              if (r) { toast(`${r.fixed} cut-out${r.fixed === 1 ? "" : "s"} redone${r.whole_photo ? `; ${r.whole_photo} now show the whole photo` : ""}`); refresh(); }
            }} disabled={busy}>
              <Sparkles size={18} /> {busy ? "Redoing cut-outs…" : "Wrong part of the photo? Redo cut-outs"}
            </button>
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
                  <button className="btn" disabled={busy || it.quantity === 0} onClick={() => adjust(-1)} aria-label="Use one"><Minus size={18} /> Use</button>
                  <button className="btn" disabled={busy} onClick={() => adjust(1)} aria-label="Add one"><Plus size={18} /></button>
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
              {it.status === "in_use" && (it.status_note || "").startsWith("Taken by ") ? (
                <button onClick={() => setPutPick(true)}><span className="ico"><Hand size={20} /></span>Put away</button>
              ) : !it.loan && (
                <button onClick={() => act(() => takeItem(it.id), "Taken; scan a label (or tap Put away) when you're done")}><span className="ico"><Hand size={20} /></span>Take it</button>
              )}
              <button onClick={() => setSheet("edit")}><span className="ico"><Pencil size={20} /></span>Edit</button>
            </div>
          )}
        </div>
      </div>

      <Section title="Details" action={edit && (
        <span className="row" style={{ gap: 4 }}>
          {!it.owner && <button className="btn small ghost" onClick={() => setPartOpen(true)}>Part details</button>}
        </span>
      )}>
        <dl className="kv">
          {it.manufacturer && (<><dt>Manufacturer</dt><dd>{it.manufacturer}</dd></>)}
          {it.model_number && (<><dt>Model</dt><dd>{it.model_number}</dd></>)}
          {it.serial_number && (<><dt>Serial</dt><dd className="mono">{it.serial_number}</dd></>)}
          {it.barcode && (<><dt>Barcode</dt><dd className="mono">{it.barcode}</dd></>)}
          {it.product_url && (<><dt>Product page</dt><dd><a href={it.product_url} target="_blank" rel="noopener noreferrer">{(() => { try { return new URL(it.product_url).hostname.replace(/^www\./, ""); } catch { return it.product_url; } })()} ↗</a></dd></>)}
          {it.expires_on && (<><dt>Expires</dt><dd>{fmtDate(it.expires_on)}{it.expired ? " (expired)" : ""}</dd></>)}
          {it.purchase_price && (<><dt>Price</dt><dd>{money(it.purchase_price, it.currency || me.app.currency)}</dd></>)}
          {(() => {
            const v = it.ai_metadata?.value_estimate;
            if (!v && !(edit && me.app.ai.enabled)) return null;
            const range = v && v.used_low !== null && v.used_high !== null ? `${money(v.used_low, v.currency)}–${money(v.used_high, v.currency)} used` : null;
            return (<><dt>Value</dt><dd>
              {v ? (
                <>
                  {v.replacement !== null ? `≈ ${money(v.replacement, v.currency)} new` : ""}{v.replacement !== null && range ? ", " : ""}{range}
                  <span className="hint" style={{ display: "block" }}>AI estimate, {fmtDate(v.estimated_on)}{v.basis ? `: ${v.basis}` : ""}</span>
                </>
              ) : null}
              {edit && me.app.ai.enabled && <button className="linkish" disabled={busy} onClick={() => act(() => api.post(`items/${it.id}/estimate-value`), "Value estimated")}>{v ? "Estimate again" : "Estimate with AI"}</button>}
            </dd></>);
          })()}
          {it.purchase_date && (<><dt>Bought</dt><dd>{fmtDate(it.purchase_date)}{it.retailer ? ` at ${it.retailer}` : ""}</dd></>)}
          {it.warranty_expires && (<><dt>Warranty</dt><dd>{it.warranty_start ? `${fmtDate(it.warranty_start)} – ` : "until "}{fmtDate(it.warranty_expires)} {it.warranty_active ? <span className="badge ok">active</span> : <span className="badge">expired</span>}</dd></>)}
          {it.product?.description && (<><dt>Description</dt><dd>{it.product.description}</dd></>)}
          {it.notes && (<><dt>Notes</dt><dd style={{ whiteSpace: "pre-line" }}>{it.notes}</dd></>)}
          <dt>Added</dt><dd>{fmtDate(it.created_at)}</dd>
        </dl>
        {managed && (
          <p className="notice" style={{ margin: "8px 0 0" }}>
            Managed equipment. <Link to={`/equipment/${it.id}`}><b>Open its equipment page</b></Link> for its checklist, parts and maintenance.
          </p>
        )}
        {edit && !managed && !it.owner && (
          <p className="muted" style={{ margin: "8px 0 0" }}>
            {isEquipmentCategory(it) ? "Equipment. " : ""}<button className="linkish" onClick={() => setTrackOpen(true)}>
              {maint.data?.profile ? "Manage as equipment again" : "Manage as equipment"}</button>
            {!isEquipmentCategory(it) && <> (for things you maintain, like a mower or generator)</>}
          </p>
        )}
      </Section>

      {me.can_edit && <CustomerItemSection item={it} edit={edit} onChanged={refresh} startRepair={doAction === "repair"} />}
      <EquipmentSection itemId={it.id} edit={edit} onChanged={() => { hist.reload(); maint.reload(); }} initial={doAction === "meter" || doAction === "service" ? doAction : undefined}
        startTracking={trackOpen} onStartClosed={() => setTrackOpen(false)} />
      <RelatedSection itemId={it.id} edit={edit} hideWhenEmpty request={addReq.related} />
      {!it.owner && <PartDetailsSection productId={it.product_id} edit={edit} open={partOpen} setOpen={setPartOpen} />}
      {!it.owner && <RestockSection productId={it.product_id} edit={edit} hideWhenEmpty request={addReq.restock} />}
      {!it.deleted_at && <TagsSection targetType="item" targetId={it.id} name={it.display_name} hideWhenEmpty request={addReq.tag} />}

      {edit && (
        // Optional extras, as one short row instead of a section each while they're empty.
        <div className="add-row">
          <span className="muted">Add:</span>
          {!docs.length && <button className="chip" onClick={() => setSheet("docs")}><Plus size={14} /> Receipt or document</button>}
          <button className="chip" onClick={() => request("related")}><Plus size={14} /> Related item</button>
          {!it.owner && <button className="chip" onClick={() => request("restock")}><Plus size={14} /> Restock alert</button>}
          <button className="chip" onClick={() => request("tag")}><Plus size={14} /> NFC tag</button>
        </div>
      )}

      {(docs.length > 0 || !!it.warranty_expires || !edit) && <Section title="Receipts & documents" action={edit && <button className="btn small" onClick={() => setSheet("docs")}><Plus size={16} /> Add</button>}>
        {docs.length ? (
          <DocumentsList itemId={it.id} docs={docs} edit={edit} onChanged={refresh}
            onRemove={(d) => window.confirm("Remove this document?") && act(() => api.del(`attachments/${d.id}`), "Removed")} />
        ) : (
          <p className="muted">No receipts, manuals or warranty documents yet.</p>
        )}
        {(it.warranty_expires || docs.length > 0) && (
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Making a warranty claim? <a href={url(`api/v1/items/${it.id}/claim-pack.zip`)} download>Download the claim pack</a> (a summary with the serial number, purchase and warranty details, plus these documents and the photos).
          </p>
        )}
      </Section>}

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
          try {
            await api.del(`items/${it.id}`, {});
            const id = it.id;
            toast(`Deleted ${it.display_name}`, false, async () => { await api.post(`items/${id}/restore`); nav(`/items/${id}`); });
            // Go to where it was kept (the box or shelf), where you'd carry on.
            const pl = it.placement;
            nav(pl.container ? `/containers/${pl.container.id}` : pl.location ? `/locations/${pl.location.id}` : "/items", { replace: true });
          } catch (err) {
            toast((err as Error).message, true);
          }
        }}>
          <Trash2 size={18} /> Delete
        </button>
      )}

      {/* Only open the form once the item for this address has loaded (Save & next
          changes the address before the next item arrives); key resets the form. */}
      {sheet === "edit" && String(it.id) === id && (
        <EditSheet
          key={it.id}
          item={it}
          onClose={() => setSheet(null)}
          onSaved={() => { setSheet(null); refresh(); }}
          onSavedNext={async () => {
            setSheet(null);
            const next = await api.get<{ items: Item[] }>("items", { needs_details: true, sort: "oldest", limit: 1 });
            nav(next.items[0] ? `/items/${next.items[0].id}?finish=1` : "/to-finish", { replace: true });
          }}
        />
      )}
      {sheet === "move" && <MoveSheet item={it} onClose={() => setSheet(null)} onUndone={refresh} onDone={(newId) => { setSheet(null); if (newId !== it.id) nav(`/items/${newId}`); else refresh(); }} />}
      {sheet === "lend" && <LendSheet item={it} onClose={() => setSheet(null)} onDone={(newId) => { setSheet(null); if (newId !== it.id) nav(`/items/${newId}`); else refresh(); }} />}
      {sheet === "status" && <StatusSheet item={it} onClose={() => setSheet(null)} onDone={() => { setSheet(null); refresh(); }} />}
      {sheet === "docs" && <DocSheet item={it} onClose={() => setSheet(null)} onDone={() => { setSheet(null); refresh(); }} />}
    </div>
  );
}

function EditSheet({ item, onClose, onSaved, onSavedNext }: { item: Item; onClose: () => void; onSaved: () => void; onSavedNext?: () => void }) {
  const [v, setV] = useState(toForm(item));
  const { busy, run } = useAction();
  const save = async (next: boolean) => {
    if (await run(() => api.patch(`items/${item.id}`, { ...toPayload(v), identification_status: "confirmed" }), "Saved")) (next && onSavedNext ? onSavedNext : onSaved)();
  };
  const suggestion = item.needs_details ? item.capture?.suggestion : null;
  return (
    <Sheet title={item.needs_details ? "Add details" : "Edit item"} onClose={onClose}>
      {item.needs_details && item.photo && <img src={url(item.photo.thumb_url || item.photo.url)} alt="" style={{ width: "100%", maxHeight: 220, objectFit: "contain", borderRadius: 12, background: "var(--surface-2)", marginBottom: 10 }} />}
      {suggestion && <p style={{ marginTop: 0 }}><Sparkles size={14} /> Pre-filled from the AI suggestion ({suggestion.confidence_label || "unknown"} confidence). Check it before saving.</p>}
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Fill from a web link</h3>
        <p className="muted" style={{ marginTop: 0 }}>Only empty fields are filled; nothing you have entered is replaced.</p>
        <LinkImport initialUrl={v.url} downloadImage={false} buttonLabel="Fill" onImported={(r) => setV((cur) => applyLinkDetails(cur, r.details, false))} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); save(false); }}>
        <ItemFields value={v} onChange={setV} sharedCount={item.siblings?.length || 0} />
        {item.needs_details && onSavedNext ? (
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn" style={{ flex: 1 }} disabled={busy} type="submit">Save</button>
            <button className="btn primary" style={{ flex: 1 }} disabled={busy} type="button" onClick={() => save(true)}>Save &amp; next</button>
          </div>
        ) : (
          <button className="btn primary block" style={{ marginTop: 14 }} disabled={busy}>Save</button>
        )}
      </form>
    </Sheet>
  );
}

function MoveSheet({ item, onClose, onDone, onUndone }: { item: Item; onClose: () => void; onDone: (id: number) => void; onUndone: () => void }) {
  const toast = useToast();
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
          const whole = qty >= item.quantity;
          const back = { container_id: item.placement.container?.id ?? null, location_id: item.placement.container ? null : item.placement.location?.id ?? null };
          const r = await run(() => api.post<Item>(`items/${item.id}/move`, { container_id: target!.container_id, location_id: target!.location_id, quantity: whole ? null : qty, temporary }));
          if (!r) return;
          // Undo puts the whole item back where it was (a part moved off stays a separate record).
          if (whole && !item.home_placement) toast(`Moved to ${target!.label}`, false, async () => { await api.post(`items/${r.id}/move`, { ...back, note: "Undo" }); onUndone(); });
          else toast("Moved");
          onDone(r.id);
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
  const toast = useToast();
  const before = { status: item.status, note: item.status_note || null };
  const options: ItemStatus[] = ["stored", "in_use", "temporarily_relocated", "awaiting_return", "missing"];
  return (
    <Sheet title="Status" onClose={onClose}>
      <label className="field" style={{ marginBottom: 10 }}>Note<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" /></label>
      <div className="stack">
        {options.map((s) => (
          <button key={s} className={`btn block ${item.status === s ? "primary" : ""}`} disabled={busy} onClick={async () => {
            if (!(await run(() => api.post(`items/${item.id}/status`, { status: s, note: note || null })))) return;
            toast(`Marked ${STATUS_LABELS[s].toLowerCase()}`, false, async () => { await api.post(`items/${item.id}/status`, before); onDone(); });
            onDone();
          }}>
            {STATUS_LABELS[s]}
          </button>
        ))}
      </div>
    </Sheet>
  );
}

function DocSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const me = useMe();
  const ai = me.app.ai.enabled;
  const [kind, setKind] = useState(ai ? "document" : "receipt");
  const { busy, run } = useAction();
  return (
    <Sheet title="Add document" onClose={onClose}>
      <div className="stack">
        <label className="field">Type
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {ai && <option value="document">Not sure – let AI work it out</option>}
            <option value="receipt">Receipt</option>
            <option value="warranty">Warranty</option>
            <option value="manual">Manual / documentation</option>
            {!ai && <option value="document">Other document</option>}
          </select>
        </label>
        {ai && <p className="muted" style={{ margin: 0 }}>AI reads it in the background, works out what it is, and offers anything it can fill in, such as the purchase date, price, shop, warranty, model or serial number. You choose what to keep.</p>}
        <label className="btn primary block">
          {busy ? <div className="spinner" /> : <FileText size={18} />} {kind === "receipt" ? "Photograph or choose receipt" : "Choose photo or PDF"}
          <input type="file" accept="image/*,application/pdf" hidden onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            if (await run(() => uploadPhoto(f, { item_id: item.id, kind }), "Added")) onDone();
          }} />
        </label>
      </div>
    </Sheet>
  );
}
