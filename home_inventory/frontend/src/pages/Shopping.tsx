import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Check, ExternalLink, Plus, ShoppingCart, Star, Trash2 } from "lucide-react";
import { api } from "../api";
import { Empty, ErrorBox, Section, Sheet, Spinner, fmtDate, money, useAction, useApi, useMe, useToast } from "../components/ui";

type Capabilities = { product_url: boolean; search_url: boolean; cart_handoff: boolean; cart_api: boolean; cart_inspection: boolean; price_lookup: boolean; availability: boolean };

interface Source {
  id: number;
  retailer: string;
  retailer_label: string;
  url: string | null;
  retailer_product_id: string | null;
  product_name: string | null;
  pack_size: number;
  estimated_price: number | null;
  preferred: boolean;
  link: string | null;
  link_kind: "product" | "search" | "none";
  capabilities: Capabilities;
}

interface RestockRow {
  product_id: number;
  item_id: number | null;
  name: string;
  stock: number;
  minimum: number | null;
  target: number | null;
  unit: string | null;
  state: "below_minimum" | "at_minimum" | "approaching" | "ok";
  suggested_quantity: number;
  estimated_cost: number | null;
  source: Source | null;
  on_list: { id: number; status: string; quantity: number }[];
  in_cart: boolean;
  last_purchased_on: string | null;
}

interface Dashboard { below_minimum: RestockRow[]; approaching: RestockRow[]; ok: RestockRow[]; needs_count: number; estimated_total: number }

interface Entry {
  id: number;
  product_id: number;
  item_id: number | null;
  name: string;
  quantity: number;
  status: "listed" | "handed_off" | "in_cart" | "purchased" | "deferred" | "cancelled";
  deferred_until: string | null;
  stock: number;
  source: Source | null;
  estimated_cost: number | null;
}

interface ShoppingList {
  groups: { retailer: string; retailer_label: string; entries: Entry[]; estimated_total: number; capabilities: Capabilities | null }[];
  open_count: number;
  providers: { key: string; name: string; id_label: string; capabilities: Capabilities }[];
}

interface Handoff {
  groups: { retailer: string; retailer_label: string; cart_url: string | null; cart_note: string; links: { entry_id: number; name: string; quantity: number; url: string | null; link_kind: string }[] }[];
}

const STATUS_TEXT: Record<Entry["status"], [string, string]> = {
  listed: ["", "On the list"],
  handed_off: ["blue", "Opened at shop"],
  in_cart: ["ok", "In cart (you confirmed)"],
  purchased: ["ok", "Bought"],
  deferred: ["", "Later"],
  cancelled: ["", "Cancelled"],
};

function CapabilityNote({ caps, label }: { caps: Capabilities | null; label: string }) {
  if (!caps) return null;
  return (
    <p className="muted" style={{ margin: "4px 0 8px", fontSize: ".82rem" }}>
      {caps.cart_handoff
        ? `${label} can show these items with an "add to cart" confirmation. Home Inventory can't see or change your cart.`
        : `${label} doesn't let other apps add to your cart: open each product and add it yourself.`}
    </p>
  );
}

// ---------------------------------------------------------------- restock page

export function ShoppingPage() {
  const me = useMe();
  const [tab, setTab] = useState<"needs" | "list">("needs");
  const dash = useApi(() => api.get<Dashboard>("replenishment"), [tab]);
  const list = useApi(() => api.get<ShoppingList>("shopping-list"), [tab]);
  const [selected, setSelected] = useState<number[]>([]);
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [buying, setBuying] = useState<Entry | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  const addToList = async (r: RestockRow) => {
    const l = await run(() => api.post<ShoppingList>("shopping-list", { product_id: r.product_id, quantity: r.suggested_quantity || 1 }), `Added ${r.name}`);
    if (l) { list.setData(l); dash.reload(); }
  };
  const patch = async (e: Entry, body: Record<string, unknown>, msg?: string) => {
    const l = await run(() => api.patch<ShoppingList>(`shopping-list/${e.id}`, body), msg);
    if (l) { list.setData(l); dash.reload(); }
  };
  const openAtShop = async (ids: number[]) => {
    const h = await run(() => api.post<Handoff>("shopping-list/handoff", { entry_ids: ids }));
    if (h) { setHandoff(h); list.reload(); }
  };
  const rows = (rs: RestockRow[]) => (
    <ul className="list">
      {rs.map((r) => (
        <li key={r.product_id}>
          <div className="grow">
            <div className="t">{r.item_id ? <Link to={`/items/${r.item_id}`}>{r.name}</Link> : r.name}</div>
            <div className="s">
              {r.stock}{r.unit ? ` ${r.unit}` : ""} in stock · min {r.minimum ?? "–"} · restock to {r.target ?? "–"}
              {r.source ? ` · ${r.source.retailer_label}` : " · no shop set"}
              {r.estimated_cost ? ` · about ${money(r.estimated_cost, me.app.currency)}` : ""}
            </div>
          </div>
          {r.on_list.length ? (
            <span className={`badge ${r.in_cart ? "ok" : "blue"}`}>{r.in_cart ? "In cart" : "On list"}</span>
          ) : r.suggested_quantity > 0 && me.can_edit ? (
            <button className="btn small primary" disabled={busy} onClick={() => addToList(r)}><Plus size={16} /> {r.suggested_quantity}</button>
          ) : null}
        </li>
      ))}
    </ul>
  );

  return (
    <div className="page">
      <div className="tabs">
        <button className={tab === "needs" ? "active" : ""} onClick={() => setTab("needs")}>Needs restocking{dash.data ? ` (${dash.data.below_minimum.length + dash.data.approaching.length})` : ""}</button>
        <button className={tab === "list" ? "active" : ""} onClick={() => setTab("list")}>Shopping list{list.data ? ` (${list.data.open_count})` : ""}</button>
      </div>
      {tab === "needs" && (
        <>
          <ErrorBox error={dash.error} />
          {!dash.data ? <Spinner /> : (
            <>
              {dash.data.below_minimum.length > 0 && <Section title="Below minimum" tight>{rows(dash.data.below_minimum)}</Section>}
              {dash.data.approaching.length > 0 && <Section title="Getting low" tight>{rows(dash.data.approaching)}</Section>}
              {!dash.data.below_minimum.length && !dash.data.approaching.length && <Empty>Nothing needs restocking. Set a minimum and restock level on an item's <b>Restock</b> section to track it.</Empty>}
              {dash.data.estimated_total > 0 && <p className="muted">Estimated cost of suggestions not yet on the list: {money(dash.data.estimated_total, me.app.currency)}</p>}
            </>
          )}
        </>
      )}
      {tab === "list" && (
        <>
          <ErrorBox error={list.error} />
          {!list.data ? <Spinner /> : list.data.groups.length ? list.data.groups.map((g) => {
            const open = g.entries.filter((e) => ["listed", "handed_off", "in_cart"].includes(e.status));
            const sel = open.filter((e) => selected.includes(e.id)).map((e) => e.id);
            return (
              <Section key={g.retailer} title={`${g.retailer_label}${g.estimated_total ? ` · about ${money(g.estimated_total, me.app.currency)}` : ""}`}>
                <CapabilityNote caps={g.capabilities} label={g.retailer_label} />
                <ul className="list">
                  {g.entries.map((e) => (
                    <li key={e.id} style={{ flexWrap: "wrap" }}>
                      {me.can_edit && ["listed", "handed_off", "in_cart"].includes(e.status) && (
                        <input type="checkbox" aria-label={`Select ${e.name}`} checked={selected.includes(e.id)} onChange={(ev) => setSelected(ev.target.checked ? [...selected, e.id] : selected.filter((x) => x !== e.id))} />
                      )}
                      <div className="grow">
                        <div className="t">{e.quantity} × {e.item_id ? <Link to={`/items/${e.item_id}`}>{e.name}</Link> : e.name}</div>
                        <div className="s">{e.stock} in stock{e.estimated_cost ? ` · about ${money(e.estimated_cost, me.app.currency)}` : ""}{e.deferred_until ? ` · later: ${fmtDate(e.deferred_until)}` : ""}</div>
                      </div>
                      <span className={`badge ${STATUS_TEXT[e.status][0]}`}>{STATUS_TEXT[e.status][1]}</span>
                      {me.can_edit && (
                        <div className="row wrap" style={{ gap: 6, width: "100%", justifyContent: "flex-end" }}>
                          {e.status === "handed_off" && <button className="btn small" disabled={busy} onClick={() => patch(e, { status: "in_cart" }, "Marked as in your cart")}><ShoppingCart size={16} /> It's in my cart</button>}
                          {["listed", "handed_off", "in_cart", "deferred"].includes(e.status) && <button className="btn small primary" disabled={busy} onClick={() => setBuying(e)}><Check size={16} /> Bought</button>}
                          {e.status !== "deferred"
                            ? <button className="btn small" disabled={busy} onClick={() => patch(e, { status: "deferred" }, "Moved to later")}>Later</button>
                            : <button className="btn small" disabled={busy} onClick={() => patch(e, { status: "listed", deferred_until: null }, "Back on the list")}>Back to list</button>}
                          <button className="iconbtn" aria-label="Remove from list" disabled={busy} onClick={() => patch(e, { status: "cancelled" }, "Removed")}><Trash2 size={18} /></button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
                {me.can_edit && open.length > 0 && (
                  <button className="btn block" style={{ marginTop: 8 }} disabled={busy} onClick={() => openAtShop(sel.length ? sel : open.map((e) => e.id))}>
                    <ExternalLink size={18} /> Open {sel.length ? `${sel.length} selected` : "all"} at {g.retailer_label}
                  </button>
                )}
              </Section>
            );
          }) : <Empty>The shopping list is empty. Add items from <b>Needs restocking</b> or an item's Restock section.</Empty>}
        </>
      )}
      {handoff && (
        <Sheet title="Open at the shop" onClose={() => { setHandoff(null); list.reload(); }}>
          <div className="stack">
            {handoff.groups.map((g) => (
              <div key={g.retailer} className="stack" style={{ gap: 6 }}>
                <b>{g.retailer_label}</b>
                <p className="muted" style={{ margin: 0 }}>{g.cart_note}</p>
                {g.cart_url && <a className="btn primary block" href={g.cart_url} target="_blank" rel="noopener noreferrer"><ShoppingCart size={18} /> Review &amp; add {g.links.length} item{g.links.length === 1 ? "" : "s"} on {g.retailer_label}</a>}
                {g.links.map((l) => (
                  <div key={l.entry_id} className="row between" style={{ gap: 8 }}>
                    <span>{l.quantity} × {l.name}</span>
                    <span className="row" style={{ gap: 6 }}>
                      <button className="btn small" onClick={() => navigator.clipboard?.writeText(String(l.quantity)).then(() => toast(`Copied quantity ${l.quantity}`))}>Qty {l.quantity}</button>
                      {l.url ? <a className="btn small" href={l.url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> {l.link_kind === "search" ? "Search" : "Product"}</a> : <span className="muted">no link</span>}
                    </span>
                  </div>
                ))}
              </div>
            ))}
            <p className="muted" style={{ margin: 0 }}>When you've added them, tap <b>It's in my cart</b> on the list. Home Inventory can't check your cart for you.</p>
          </div>
        </Sheet>
      )}
      {buying && <PurchasedSheet entry={buying} onClose={() => setBuying(null)} onDone={(l) => { setBuying(null); list.setData(l); dash.reload(); }} />}
    </div>
  );
}

function PurchasedSheet({ entry, onClose, onDone }: { entry: Entry; onClose: () => void; onDone: (l: ShoppingList) => void }) {
  const [qty, setQty] = useState(String(entry.quantity));
  const [price, setPrice] = useState(entry.source?.estimated_price ? String(entry.source.estimated_price) : "");
  const [addToStock, setAddToStock] = useState(true);
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const l = await run(() => api.post<ShoppingList>(`shopping-list/${entry.id}/purchased`, { quantity: Number(qty), unit_price: price === "" ? null : price, add_to_stock: addToStock }), "Recorded");
    if (l) onDone(l);
  };
  return (
    <Sheet title={`Bought ${entry.name}`} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="grid two">
          <label className="field">Quantity<input type="number" inputMode="numeric" min={1} required value={qty} onChange={(e) => setQty(e.target.value)} /></label>
          <label className="field">Price each (optional)<input type="number" inputMode="decimal" step="0.01" min={0} value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        </div>
        <label className="row"><input type="checkbox" checked={addToStock} onChange={(e) => setAddToStock(e.target.checked)} /> Add to my stock now ({entry.stock} → {entry.stock + (Number(qty) || 0)})</label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------- item page section

interface RestockDetail {
  profile: { enabled: boolean; configured: boolean; target_stock: number | null; reorder_quantity: number | null; unit: string | null; notes: string | null; last_purchased_on: string | null; last_price: number | null };
  status: RestockRow;
  sources: Source[];
  providers: ShoppingList["providers"];
}

export function RestockSection({ productId, edit }: { productId: number; edit: boolean }) {
  const me = useMe();
  const d = useApi(() => api.get<RestockDetail>(`products/${productId}/restock`), [productId]);
  const [sheet, setSheet] = useState<null | "settings" | "source">(null);
  const [editSource, setEditSource] = useState<Source | null>(null);
  const { busy, run } = useAction();
  if (!d.data) return null;
  const x = d.data;
  const tracked = x.status.minimum !== null || x.profile.configured;
  if (!tracked && !x.sources.length && !edit) return null;
  const addToList = async () => {
    if (await run(() => api.post("shopping-list", { product_id: productId }), "Added to the shopping list")) d.reload();
  };
  const remove = async (s: Source) => {
    if (!window.confirm(`Remove ${s.retailer_label}?`)) return;
    await run(() => api.del(`sources/${s.id}`), "Removed");
    d.reload();
  };
  const stateBadge = { below_minimum: ["danger", "Below minimum"], at_minimum: ["warn", "At minimum"], approaching: ["warn", "Getting low"], ok: ["ok", "Stocked"] }[x.status.state];
  return (
    <Section title="Restock" action={edit && <button className="btn small" onClick={() => setSheet("settings")}>Settings</button>}>
      {tracked ? (
        <div className="row between wrap" style={{ marginBottom: 8 }}>
          <span>{x.status.stock}{x.status.unit ? ` ${x.status.unit}` : ""} in stock · minimum {x.status.minimum ?? "–"} · restock to {x.status.target ?? "–"} <span className={`badge ${stateBadge[0]}`}>{stateBadge[1]}</span></span>
          {x.status.on_list.length ? <span className={`badge ${x.status.in_cart ? "ok" : "blue"}`}>{x.status.in_cart ? "In cart" : "On shopping list"}</span>
            : edit && <button className="btn small primary" disabled={busy} onClick={addToList}><Plus size={16} /> Shopping list{x.status.suggested_quantity ? ` (${x.status.suggested_quantity})` : ""}</button>}
        </div>
      ) : <p className="muted" style={{ marginTop: 0 }}>Set a minimum to get "running low" alerts and restock suggestions.</p>}
      {x.sources.length > 0 && (
        <ul className="list">
          {x.sources.map((s) => (
            <li key={s.id}>
              {s.preferred ? <Star size={16} aria-label="Preferred" /> : <span style={{ width: 16 }} />}
              <div className="grow">
                <div className="t">{s.retailer_label}{s.retailer_product_id ? <span className="mono"> {s.retailer_product_id}</span> : null}</div>
                <div className="s">{s.pack_size > 1 ? `pack of ${s.pack_size} · ` : ""}{s.estimated_price !== null ? `about ${money(s.estimated_price, me.app.currency)}` : "no price"}{s.capabilities.cart_handoff ? " · can hand off to cart" : " · product link only"}</div>
              </div>
              {s.link && <a className="btn small" href={s.link} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> {s.link_kind === "search" ? "Search" : "Open"}</a>}
              {edit && <button className="btn small" onClick={() => { setEditSource(s); setSheet("source"); }}>Edit</button>}
              {edit && <button className="iconbtn" aria-label={`Remove ${s.retailer_label}`} onClick={() => remove(s)}><Trash2 size={16} /></button>}
            </li>
          ))}
        </ul>
      )}
      {edit && <button className="btn small" style={{ marginTop: 8 }} onClick={() => { setEditSource(null); setSheet("source"); }}><Plus size={16} /> Where to buy</button>}
      {x.profile.last_purchased_on && <p className="muted" style={{ marginBottom: 0 }}>Last bought {fmtDate(x.profile.last_purchased_on)}{x.profile.last_price !== null ? ` at ${money(x.profile.last_price, me.app.currency)}` : ""}</p>}
      {sheet === "settings" && <RestockSettingsSheet productId={productId} detail={x} onClose={() => setSheet(null)} onDone={(n) => { d.setData(n); setSheet(null); }} />}
      {sheet === "source" && <SourceSheet productId={productId} providers={x.providers} source={editSource} onClose={() => setSheet(null)} onDone={(n) => { d.setData(n); setSheet(null); }} />}
    </Section>
  );
}

function RestockSettingsSheet({ productId, detail, onClose, onDone }: { productId: number; detail: RestockDetail; onClose: () => void; onDone: (d: RestockDetail) => void }) {
  const n = (v: number | null) => (v === null ? "" : String(v));
  const [f, setF] = useState({ min_quantity: n(detail.status.minimum), target_stock: n(detail.profile.target_stock), reorder_quantity: n(detail.profile.reorder_quantity), unit: detail.profile.unit || "", notes: detail.profile.notes || "" });
  const [enabled, setEnabled] = useState(detail.profile.configured ? detail.profile.enabled : true);
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const num = (v: string) => (v === "" ? null : Number(v));
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const d = await run(() => api.put<RestockDetail>(`products/${productId}/restock`, {
      enabled, min_quantity: num(f.min_quantity), target_stock: num(f.target_stock), reorder_quantity: num(f.reorder_quantity), unit: f.unit, notes: f.notes,
    }), "Saved");
    if (d) onDone(d);
  };
  return (
    <Sheet title="Restock settings" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="row"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Include in restock suggestions</label>
        <div className="grid two">
          <label className="field">Minimum<input type="number" inputMode="numeric" min={0} value={f.min_quantity} onChange={set("min_quantity")} /><span className="hint">Below this it's "running low"</span></label>
          <label className="field">Restock to<input type="number" inputMode="numeric" min={0} value={f.target_stock} onChange={set("target_stock")} /><span className="hint">Suggested buys fill up to this</span></label>
          <label className="field">Buy at least<input type="number" inputMode="numeric" min={1} value={f.reorder_quantity} onChange={set("reorder_quantity")} /></label>
          <label className="field">Unit<input value={f.unit} onChange={set("unit")} placeholder="qt, rolls, filters…" /></label>
        </div>
        <label className="field">Notes<textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

function SourceSheet({ productId, providers, source, onClose, onDone }: { productId: number; providers: ShoppingList["providers"]; source: Source | null; onClose: () => void; onDone: (d: RestockDetail) => void }) {
  const [retailer, setRetailer] = useState(source?.retailer || "amazon");
  const [f, setF] = useState({ url: source?.url || "", retailer_product_id: source?.retailer_product_id || "", retailer_name: source?.retailer_label && source.retailer === "other" ? source.retailer_label : "", pack_size: String(source?.pack_size || 1), estimated_price: source?.estimated_price !== null && source?.estimated_price !== undefined ? String(source.estimated_price) : "" });
  const [preferred, setPreferred] = useState(source ? source.preferred : false);
  const { busy, run } = useAction();
  const prov = providers.find((p) => p.key === retailer)!;
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body = { retailer, url: f.url || null, retailer_product_id: f.retailer_product_id || null, retailer_name: retailer === "other" ? f.retailer_name || null : null, pack_size: Number(f.pack_size) || 1, estimated_price: f.estimated_price === "" ? null : f.estimated_price, preferred };
    const d = await run(() => (source ? api.patch<RestockDetail>(`sources/${source.id}`, body) : api.post<RestockDetail>(`products/${productId}/sources`, body)), "Saved");
    if (d) onDone(d);
  };
  return (
    <Sheet title={source ? "Edit shop" : "Where to buy"} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="steps">
          {providers.map((p) => <button key={p.key} type="button" className={retailer === p.key ? "active" : ""} onClick={() => setRetailer(p.key)}>{p.name}</button>)}
        </div>
        <p className="muted" style={{ margin: 0, fontSize: ".85rem" }}>
          {prov.capabilities.cart_handoff ? `${prov.name}: product link, and an "add to cart" link you confirm on ${prov.name}.` : `${prov.name}: product link only. Adding to the cart is done by you on the shop's site.`}
        </p>
        {retailer === "other" && <label className="field">Shop name<input value={f.retailer_name} onChange={set("retailer_name")} /></label>}
        <label className="field">Product page link<input type="url" inputMode="url" value={f.url} onChange={set("url")} placeholder="https://…" /><span className="hint">Paste the product page; the {prov.id_label} is read from it where possible.</span></label>
        {retailer !== "other" && <label className="field">{prov.id_label}<input value={f.retailer_product_id} onChange={set("retailer_product_id")} /></label>}
        <div className="grid two">
          <label className="field">Pack size<input type="number" inputMode="numeric" min={1} value={f.pack_size} onChange={set("pack_size")} /></label>
          <label className="field">Price (optional)<input type="number" inputMode="decimal" step="0.01" min={0} value={f.estimated_price} onChange={set("estimated_price")} /></label>
        </div>
        <label className="row"><input type="checkbox" checked={preferred} onChange={(e) => setPreferred(e.target.checked)} /> Preferred shop for this item</label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}
