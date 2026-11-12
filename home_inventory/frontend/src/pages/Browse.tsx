import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { TagsSection } from "./Tags";
import { AlertTriangle, Box, Camera, ChevronDown, ChevronRight, Clock, FileSpreadsheet, Images, Map as MapIcon, MapPin, Package, Pencil, Plus, Printer, QrCode, ScanBarcode, Search as SearchIcon, Sparkles, Trash2 } from "lucide-react";
import { api, qrImageUrl, type Dashboard, type Item, type LocationDetail, type LocationNode, type SearchResult } from "../api";
import { LocationImportSheet } from "../components/LocationImport";
import { PlacementPicker } from "../components/PlacementPicker";
import { ContainerFormSheet } from "./Containers";
import { ContainerRow, Empty, ErrorBox, ItemRow, Section, Sheet, Spinner, fmtDate, fmtWhen, money, useAction, useApi, useMe } from "../components/ui";

export function SearchBox({ hero, initial = "" }: { hero?: boolean; initial?: string }) {
  const [q, setQ] = useState(initial);
  const nav = useNavigate();
  useEffect(() => setQ(initial), [initial]);
  return (
    <form className={`searchbox ${hero ? "hero" : ""}`} onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`); }} role="search">
      <SearchIcon size={20} />
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={hero ? 'Ask: "Where are my axle stands?"' : "Search inventory"} aria-label="Search inventory" enterKeyHint="search" />
    </form>
  );
}

export function DashboardPage() {
  const me = useMe();
  const d = useApi(() => api.get<Dashboard>("dashboard"), []);
  const s = d.data?.stats;
  return (
    <div className="page">
      <SearchBox hero />
      <div className="quick" style={{ margin: "14px 0" }}>
        {me.can_edit && <Link to="/add"><span className="ico"><Plus size={20} /></span>Add item</Link>}
        <Link to="/scan"><span className="ico"><ScanBarcode size={20} /></span>Scan barcode</Link>
        <Link to="/scan"><span className="ico"><QrCode size={20} /></span>Scan container</Link>
        {me.can_edit && <Link to="/containers?new=1"><span className="ico"><Box size={20} /></span>Add container</Link>}
        {me.can_edit && <Link to="/capture" style={{ gridColumn: "1 / -1" }}><span className="ico"><Camera size={20} /></span>Quick photos — snap now, sort onto shelves later</Link>}
      </div>
      <ErrorBox error={d.error} />
      {!s ? <Spinner /> : (
        <>
          <div className="grid stats" style={{ marginBottom: 14 }}>
            <Link className="stat" to="/items"><div className="v">{s.total_items}</div><div className="l">Items</div></Link>
            <Link className="stat" to="/containers"><div className="v">{s.containers}</div><div className="l">Containers</div></Link>
            <Link className={`stat ${s.loans_overdue ? "alert" : ""}`} to="/loans"><div className="v">{s.on_loan}</div><div className="l">On loan{s.loans_overdue ? ` · ${s.loans_overdue} overdue` : ""}</div></Link>
            <Link className={`stat ${s.expired ? "alert" : ""}`} to="/loans?tab=expiry"><div className="v">{s.expiring_soon + s.expired}</div><div className="l">Expiring / expired</div></Link>
            {(s.repairs_awaiting_pickup ?? 0) > 0 && <Link className="stat" to="/repairs"><div className="v" style={{ color: "var(--ok)" }}>{s.repairs_awaiting_pickup}</div><div className="l">Repairs ready for pickup</div></Link>}
            {(s.maintenance_overdue ?? 0) > 0 && <Link className="stat alert" to="/maintenance"><div className="v">{s.maintenance_overdue}</div><div className="l">Maintenance overdue</div></Link>}
            {(s.unsorted_photos ?? 0) > 0 && <Link className="stat" to="/sort"><div className="v" style={{ color: "var(--primary)" }}>{s.unsorted_photos}</div><div className="l">To sort (no place yet)</div></Link>}
            {s.needs_details > 0 && <Link className="stat" to="/to-finish"><div className="v" style={{ color: "var(--primary)" }}>{s.needs_details}</div><div className="l">To finish (quick photos)</div></Link>}
            {s.low_stock > 0 && <Link className="stat alert" to="/loans?tab=stock"><div className="v">{s.low_stock}</div><div className="l">Running low</div></Link>}
            {s.total_value > 0 && <div className="stat"><div className="v" style={{ fontSize: "1.2rem" }}>{money(s.total_value, s.currency)}</div><div className="l">Recorded value</div></div>}
          </div>
          {d.data!.loans.length > 0 && (
            <Section title="Out on loan" action={<Link to="/loans">All</Link>} tight>
              <div className="list">{d.data!.loans.map((l) => <ItemRow key={l.id} item={l.item} right={<div className="s" style={{ textAlign: "right" }}>{l.borrower}<br />{l.expected_return ? `due ${fmtDate(l.expected_return)}` : ""}</div>} />)}</div>
            </Section>
          )}
          {(d.data!.expiring.length > 0 || d.data!.low_stock.length > 0) && (
            <Section title="Use soon & restock" action={<Link to="/loans?tab=expiry">All</Link>} tight>
              <div className="list">
                {d.data!.expiring.map((i) => <ItemRow key={i.id} item={i} right={<div className="s" style={{ textAlign: "right" }}>{i.expired ? "expired" : "expires"}<br />{fmtDate(i.expires_on)}</div>} />)}
                {d.data!.low_stock.map((p) => (
                  <Link key={`p${p.product_id}`} to={p.item_id ? `/items/${p.item_id}` : "/items"}>
                    <AlertTriangle size={22} color="var(--warn)" />
                    <div className="grow"><div className="t">{p.name}</div><div className="s">Running low — {p.quantity} left (alert below {p.min_quantity})</div></div>
                  </Link>
                ))}
              </div>
            </Section>
          )}
          {d.data!.warranties.length > 0 && (
            <Section title="Warranties ending soon" action={<Link to="/loans?tab=warranty">All</Link>} tight>
              <div className="list">{d.data!.warranties.map((i) => <ItemRow key={i.id} item={i} right={<div className="s">{fmtDate(i.warranty_expires)}</div>} />)}</div>
            </Section>
          )}
          <Section title="Recent activity" action={<Link to="/activity">All</Link>} tight>
            {d.data!.recent_activity.length ? (
              <div className="list">{d.data!.recent_activity.map((e) => <li key={e.id}><Clock size={18} className="muted" /><div className="grow"><div style={{ fontWeight: 500 }}>{e.summary}</div><div className="s">{e.user} · {fmtWhen(e.ts)}</div></div></li>)}</div>
            ) : <Empty>Nothing yet. Start by creating locations (e.g. House › Garage), then add containers and items.</Empty>}
          </Section>
        </>
      )}
    </div>
  );
}

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") || "";
  const r = useApi(() => (q ? api.get<SearchResult>("search", { q }) : Promise.resolve(null)), [q]);
  return (
    <div className="page">
      <SearchBox hero initial={q} />
      <div style={{ height: 14 }} />
      {!q ? <Empty>Try “How many USB-C chargers do I own?”, “Find my Christmas decorations” or “Did I lend anyone my impact wrench?”</Empty> : r.loading ? <Spinner /> : (
        <>
          <ErrorBox error={r.error} />
          {r.data && (
            <>
              <div className="answer">
                {r.data.answer}
                {r.data.interpretation.by.startsWith("ai") && <div className="muted" style={{ fontSize: ".8rem", marginTop: 4 }}><Sparkles size={12} /> Question interpreted by AI; results come from your inventory.</div>}
              </div>
              <div className="card tight">
                {r.data.results.length ? <div className="list">{r.data.results.map((i) => <ItemRow key={i.id} item={i} />)}</div> : <Empty>No matches.</Empty>}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

export function ItemsPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const sort = params.get("sort") || "name";
  const status = params.get("status") || "";
  const loose = params.get("loose") === "1";
  const cats = useApi(() => api.get<{ id: number; name: string; item_count: number }[]>("categories"), []);
  const category = params.get("category") || "";
  const list = useApi(() => api.get<{ total: number; items: Item[] }>("items", { q, sort, status, category, loose: loose || undefined, limit: 300 }), [q, sort, status, category, loose]);
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }); };
  return (
    <div className="page">
      <div className="row between"><h1>Items</h1><Link className="btn primary" to="/add"><Plus size={18} /> Add</Link></div>
      <input placeholder="Filter by name, maker, model, serial…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="row wrap" style={{ margin: "10px 0" }}>
        <select value={category} onChange={(e) => set("category", e.target.value)} style={{ width: "auto", flex: 1 }}>
          <option value="">All categories</option>
          {cats.data?.map((c) => <option key={c.id} value={c.name}>{c.name} ({c.item_count})</option>)}
        </select>
        <select value={status} onChange={(e) => set("status", e.target.value)} style={{ width: "auto", flex: 1 }}>
          <option value="">Any status</option><option value="stored">In storage</option><option value="in_use">In use</option><option value="temporarily_relocated">Temporarily moved</option><option value="on_loan">Lent out</option><option value="missing">Missing</option>
        </select>
        <select value={sort} onChange={(e) => set("sort", e.target.value)} style={{ width: "auto", flex: 1 }}>
          <option value="name">A–Z</option><option value="recent">Recently changed</option><option value="created">Newest</option><option value="value">Value</option><option value="warranty">Warranty end</option><option value="expiry">Expiry date</option>
        </select>
        <label className="check"><input type="checkbox" checked={loose} onChange={(e) => set("loose", e.target.checked ? "1" : "")} /> Loose only</label>
      </div>
      <div className="card tight">
        {list.loading && !list.data ? <Spinner /> : list.data?.items.length ? <div className="list">{list.data.items.map((i) => <ItemRow key={i.id} item={i} />)}</div> : <Empty>No items.</Empty>}
      </div>
      {list.data && <p className="muted">{list.data.total} record{list.data.total === 1 ? "" : "s"}</p>}
    </div>
  );
}

function TreeNode({ n }: { n: LocationNode }) {
  const [open, setOpen] = useState(true);
  return (
    <li>
      <div className="tree-row">
        {n.children.length ? <button className="iconbtn" style={{ width: 28, height: 28 }} onClick={() => setOpen(!open)} aria-label={open ? "Collapse" : "Expand"}>{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button> : <span style={{ width: 28 }} />}
        <MapPin size={16} className="muted" />
        <Link to={`/locations/${n.id}`}>{n.name}</Link>
        <span className="muted" style={{ fontSize: ".8rem" }}>{n.item_count ? `${n.item_count} loose` : ""}{n.container_count ? ` ${n.container_count} ctr` : ""}</span>
      </div>
      {open && n.children.length > 0 && <ul className="tree">{n.children.map((c) => <TreeNode key={c.id} n={c} />)}</ul>}
    </li>
  );
}

export function LocationsPage() {
  const me = useMe();
  const tree = useApi(() => api.get<LocationNode[]>("locations/tree"), []);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  return (
    <div className="page">
      <div className="row between wrap" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0 }}>Locations</h1>
        {me.can_edit && (
          <div className="row wrap">
            <button className="btn" onClick={() => setImporting(true)}><FileSpreadsheet size={18} /> Import from Excel</button>
            <button className="btn primary" onClick={() => setAdding(true)}><Plus size={18} /> New</button>
          </div>
        )}
      </div>
      <p className="muted">Nest places as deep as you like: Property › Shed › Right side › Ground level. Items can live directly in a location (shelves, drawers, workbenches) or in containers.</p>
      <div className="card">
        {tree.loading && !tree.data ? <Spinner /> : tree.data?.length ? <ul className="tree">{tree.data.map((n) => <TreeNode key={n.id} n={n} />)}</ul> : <Empty>No locations yet. Add them one by one, or use <b>Import from Excel</b> to create your whole hierarchy at once.</Empty>}
      </div>
      {adding && <LocationFormSheet onClose={() => setAdding(false)} onSaved={() => { setAdding(false); tree.reload(); }} />}
      {importing && <LocationImportSheet onClose={() => setImporting(false)} onDone={() => { setImporting(false); tree.reload(); }} />}
    </div>
  );
}

const KINDS = ["Property", "Building", "Room", "Area", "Rack", "Shelf", "Drawer", "Cabinet", "Closet", "Workbench", "Tool rack", "Wall"];

function LocationFormSheet({ parentId, loc, onClose, onSaved }: { parentId?: number | null; loc?: LocationDetail; onClose: () => void; onSaved: (id: number) => void }) {
  const [name, setName] = useState(loc?.name || "");
  const [kind, setKind] = useState(loc?.kind || "");
  const [desc, setDesc] = useState(loc?.description || "");
  const { busy, run } = useAction();
  return (
    <Sheet title={loc ? "Edit location" : "New location"} onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault();
        const body = { name, kind: kind || null, description: desc || null };
        const r = loc ? await run(() => api.patch<{ id: number }>(`locations/${loc.id}`, body), "Saved") : await run(() => api.post<{ id: number }>("locations", { ...body, parent_id: parentId ?? null }), "Location added");
        if (r) onSaved(r.id);
      }}>
        <label className="field">Name<input required autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Shelf 2" /></label>
        <label className="field">Kind<input list="kinds" value={kind} onChange={(e) => setKind(e.target.value)} /><datalist id="kinds">{KINDS.map((k) => <option key={k} value={k} />)}</datalist></label>
        <label className="field">Description<textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

export function LocationDetailPage() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const d = useApi(() => api.get<LocationDetail>(`locations/${id}`), [id]);
  const [sheet, setSheet] = useState<null | "child" | "edit" | "move" | "container" | "qr">(null);
  const { run } = useAction();
  if (d.loading && !d.data) return <Spinner />;
  if (!d.data) return <div className="page"><ErrorBox error={d.error} /></div>;
  const l = d.data;
  return (
    <div className="page">
      <div className="path" style={{ marginBottom: 4 }}>{l.path_parts.slice(0, -1).map((p) => <span key={p.id} className="row" style={{ gap: 4 }}><Link to={`/locations/${p.id}`}>{p.name}</Link><ChevronRight size={12} /></span>)}</div>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <h1 style={{ flex: 1 }}>{l.name} {l.kind && <span className="badge">{l.kind}</span>}</h1>
        <button className="iconbtn" onClick={() => setSheet("qr")} aria-label="Show QR label"><QrCode size={26} /></button>
      </div>
      {l.description && <p>{l.description}</p>}
      {me.can_edit && (
        <div className="quick" style={{ marginBottom: 14 }}>
          <Link to={`/add?location_id=${l.id}&return=/locations/${l.id}`}><span className="ico"><Package size={20} /></span>Add loose item</Link>
          <Link to={`/capture?location_id=${l.id}`}><span className="ico"><Images size={20} /></span>Quick photos</Link>
          <button onClick={() => setSheet("container")}><span className="ico"><Box size={20} /></span>Add container</button>
          <button onClick={() => setSheet("child")}><span className="ico"><MapPin size={20} /></span>Add sub-location</button>
          <Link to={`/maps?location=${l.id}`}><span className="ico"><MapIcon size={20} /></span>Visual maps</Link>
        </div>
      )}
      {l.children!.length > 0 && <Section title="Inside" tight><div className="list">{l.children!.map((c) => <Link key={c.id} to={`/locations/${c.id}`}><MapPin size={20} /><div className="grow"><div className="t">{c.name}</div>{c.kind && <div className="s">{c.kind}</div>}</div><ChevronRight size={18} /></Link>)}</div></Section>}
      {l.containers!.length > 0 && <Section title="Containers" tight><div className="list">{l.containers!.map((c) => <ContainerRow key={c.id} c={c} />)}</div></Section>}
      <Section title="Loose items" tight>{l.items!.length ? <div className="list">{l.items!.map((i) => <ItemRow key={i.id} item={i} />)}</div> : <Empty>No loose items here.</Empty>}</Section>
      <TagsSection targetType="location" targetId={l.id} name={l.name} />
      {l.maps!.length > 0 && <Section title="Visual maps">{l.maps!.map((m) => <Link key={m.id} className="btn" to={`/maps/${m.id}`} style={{ marginRight: 8 }}>{m.title}</Link>)}</Section>}
      {me.can_edit && (
        <div className="row wrap">
          <button className="btn" onClick={() => setSheet("edit")}><Pencil size={18} /> Edit</button>
          <button className="btn" onClick={() => setSheet("move")}>Move under…</button>
          <span className="spacer" />
          <button className="btn danger" onClick={async () => { if (window.confirm(`Delete ${l.name}? It must be empty.`) && (await run(() => api.del(`locations/${l.id}`).then(() => true), "Deleted"))) nav("/locations"); }}><Trash2 size={18} /> Delete</button>
        </div>
      )}
      {sheet === "qr" && (
        <Sheet title="QR label" onClose={() => setSheet(null)}>
          <div className="stack" style={{ alignItems: "center" }}>
            <img src={qrImageUrl("locations", l.id)} alt={`QR code ${l.qr_code}`} style={{ width: 240, background: "#fff", borderRadius: 12, padding: 8 }} />
            <div className="mono" style={{ fontSize: "1.2rem", fontWeight: 700 }}>{l.qr_code}</div>
            <p className="muted" style={{ textAlign: "center" }}>Stick this on the shelf, cabinet or drawer. Scanning it with your phone's camera shows everything stored here, including the containers.</p>
            <Link className="btn primary block" to={`/labels?locs=${l.id}`}><Printer size={18} /> Print label</Link>
          </div>
        </Sheet>
      )}
      {sheet === "child" && <LocationFormSheet parentId={l.id} onClose={() => setSheet(null)} onSaved={(nid) => nav(`/locations/${nid}`)} />}
      {sheet === "edit" && <LocationFormSheet loc={l} onClose={() => setSheet(null)} onSaved={() => { setSheet(null); d.reload(); }} />}
      {sheet === "container" && <ContainerFormSheet initial={{ location_id: l.id, container_id: null, label: l.path }} onClose={() => setSheet(null)} onSaved={(c) => nav(`/containers/${c.id}`)} />}
      {sheet === "move" && <PlacementPicker title="Move this location under…" allowContainers={false} onClose={() => setSheet(null)} onPick={async (p) => { setSheet(null); await run(() => api.patch(`locations/${l.id}`, { parent_id: p.location_id }), "Moved"); d.reload(); }} />}
    </div>
  );
}
