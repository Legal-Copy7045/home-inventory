import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowRightLeft, Camera, Home, Pencil, Plus, Printer, QrCode, ScanLine, Sparkles, Trash2 } from "lucide-react";
import { api, qrImageUrl, url, type Container, type HistoryEvent, type Scan } from "../api";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { ContainerRow, Empty, ErrorBox, ItemRow, PathView, Section, Sheet, Spinner, Stepper, fmtWhen, useAction, useApi, useMe, useToast } from "../components/ui";

export function ContainersPage() {
  const me = useMe();
  const [q, setQ] = useState("");
  const list = useApi(() => api.get<Container[]>("containers", { q }), [q]);
  const [params] = useSearchParams();
  const [creating, setCreating] = useState(params.get("new") === "1");
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="row between"><h1>Containers</h1>{me.can_edit && <button className="btn primary" onClick={() => setCreating(true)}><Plus size={18} /> New</button>}</div>
      <input placeholder="Filter by name or number" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />
      <ErrorBox error={list.error} />
      <div className="card tight">
        {list.loading && !list.data ? <Spinner /> : list.data?.length ? (
          <div className="list">{list.data.map((c) => <ContainerRow key={c.id} c={c} />)}</div>
        ) : <Empty>No containers yet. Create boxes, bins and crates, then print QR labels for them.</Empty>}
      </div>
      {creating && <ContainerFormSheet onClose={() => setCreating(false)} onSaved={(c) => nav(`/containers/${c.id}`)} />}
    </div>
  );
}

export function ContainerFormSheet({ container, onClose, onSaved, initial }: { container?: Container; onClose: () => void; onSaved: (c: Container) => void; initial?: PlacementValue }) {
  const [name, setName] = useState(container?.name || "");
  const [number, setNumber] = useState(container?.number || "");
  const [type, setType] = useState(container?.container_type || "");
  const [desc, setDesc] = useState(container?.description || "");
  const [place, setPlace] = useState<PlacementValue>(initial || { container_id: null, location_id: null, label: "Unassigned" });
  const [picking, setPicking] = useState(false);
  const { busy, run } = useAction();
  return (
    <Sheet title={container ? "Edit container" : "New container"} onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault();
        const body = { name, number: number || null, container_type: type || null, description: desc || null };
        const r = container
          ? await run(() => api.patch<Container>(`containers/${container.id}`, body), "Saved")
          : await run(() => api.post<Container>("containers", { ...body, location_id: place.location_id, parent_container_id: place.container_id }), "Container created");
        if (r) onSaved(r);
      }}>
        <div className="grid two">
          <label className="field">Name<input required autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Christmas lights" /></label>
          <label className="field">Number<input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="e.g. 15" /></label>
        </div>
        <label className="field">Type
          <input list="ctype" value={type} onChange={(e) => setType(e.target.value)} placeholder="Box, tote, bin, drawer unit…" />
          <datalist id="ctype">{["Cardboard box", "Plastic tote", "Bin", "Crate", "Bag", "Case", "Drawer unit", "Toolbox", "Suitcase"].map((t) => <option key={t} value={t} />)}</datalist>
        </label>
        <label className="field">Description<textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
        {!container && (
          <label className="field">Where is it?
            <button type="button" className="btn block" style={{ justifyContent: "flex-start" }} onClick={() => setPicking(true)}>{place.label}</button>
          </label>
        )}
        <button className="btn primary block" disabled={busy}>{container ? "Save" : "Create"}</button>
      </form>
      {picking && <PlacementPicker title="Put the container in…" onClose={() => setPicking(false)} onPick={(p) => { setPlace(p); setPicking(false); }} />}
    </Sheet>
  );
}

export function ContainerDetail() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const c = useApi(() => api.get<Container>(`containers/${id}`), [id]);
  const scans = useApi(() => api.get<Scan[]>(`containers/${id}/scans`), [id]);
  const hist = useApi(() => api.get<{ events: HistoryEvent[] }>("history", { container_id: id, limit: 20 }), [id]);
  const [sheet, setSheet] = useState<null | "edit" | "move" | "qr" | "scan">(params.get("scan") ? "scan" : null);
  const [scan, setScan] = useState<Scan | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  if (c.loading && !c.data) return <Spinner />;
  if (!c.data) return <div className="page"><ErrorBox error={c.error} /></div>;
  const ct = c.data;
  const edit = me.can_edit && !ct.deleted_at;
  const refresh = () => { c.reload(); hist.reload(); scans.reload(); };
  const pending = scans.data?.find((s) => s.status === "ready");

  async function analyse(file: File) {
    setSheet("scan");
    setScan({ id: 0, container_id: ct.id, status: "analysing", provider: null, model: null, error: null, photo: null, suggestions: [], created_at: "" });
    const f = new FormData();
    f.append("file", file);
    try {
      const s = await api.upload<Scan>(`containers/${ct.id}/scans`, f);
      setScan(s);
      scans.reload();
    } catch (e) {
      toast((e as Error).message, true);
      setSheet(null);
    }
  }

  return (
    <div className="page">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="grow" style={{ flex: 1, minWidth: 0 }}>
          <div className="row wrap" style={{ gap: 6 }}>
            {ct.number && <span className="badge blue" style={{ fontSize: ".95rem" }}>#{ct.number}</span>}
            {ct.container_type && <span className="badge">{ct.container_type}</span>}
            {ct.temporarily_moved && <span className="badge warn">Temporarily moved</span>}
          </div>
          <h1 style={{ margin: "6px 0 4px" }}>{ct.name}</h1>
          <PathView placement={ct.placement} />
          {ct.home_placement && <div className="muted" style={{ marginTop: 4 }}>Normally: {ct.home_placement.path}</div>}
          {ct.description && <p style={{ marginTop: 8 }}>{ct.description}</p>}
        </div>
        <button className="iconbtn" onClick={() => setSheet("qr")} aria-label="Show QR label"><QrCode size={26} /></button>
      </div>

      {edit && (
        <div className="quick" style={{ margin: "14px 0" }}>
          <Link to={`/add?container_id=${ct.id}&return=/containers/${ct.id}`}><span className="ico"><Plus size={20} /></span>Add item</Link>
          {me.app.ai.enabled && <label style={{ cursor: "pointer" }}>
            <span className="ico"><Sparkles size={20} /></span>Photo inventory
            <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) analyse(f); }} />
          </label>}
          <button onClick={() => setSheet("move")}><span className="ico"><ArrowRightLeft size={20} /></span>Move</button>
          <Link to={`/scan?container=${ct.id}`}><span className="ico"><ScanLine size={20} /></span>Scan mode</Link>
        </div>
      )}
      {pending && sheet !== "scan" && (
        <div className="notice row between">
          <span>A photo inventory is waiting for review ({pending.suggestions.length} suggestions).</span>
          <button className="btn small" onClick={() => { setScan(pending); setSheet("scan"); }}>Review</button>
        </div>
      )}
      {!me.app.ai.enabled && edit && <p className="muted">Photo inventory needs an AI provider (see Settings). You can always add items manually.</p>}

      <Section title={`Contents (${ct.items?.reduce((a, i) => a + i.quantity, 0) || 0})`} tight>
        {ct.items?.length || ct.containers?.length ? (
          <div className="list">
            {ct.containers?.map((cc) => <ContainerRow key={`c${cc.id}`} c={cc} />)}
            {ct.items?.map((i) => <ItemRow key={i.id} item={i} />)}
          </div>
        ) : <Empty>Empty. Add items, or photograph the open container and let AI suggest what's inside.</Empty>}
      </Section>

      {ct.photos && ct.photos.length > 0 && (
        <Section title="Photos">
          <div className="photos">{ct.photos.map((p) => <a key={p.id} className="ph" href={url(p.url)} target="_blank" rel="noreferrer"><img src={url(p.thumb_url || p.url)} alt="" /></a>)}</div>
        </Section>
      )}
      {ct.maps && ct.maps.length > 0 && <Section title="Visual maps">{ct.maps.map((m) => <Link key={m.id} className="btn" to={`/maps/${m.id}`}>{m.title}</Link>)}</Section>}

      <Section title="Activity" tight>
        <div className="list">{hist.data?.events.map((e) => <li key={e.id}><div className="grow"><div style={{ fontWeight: 500 }}>{e.summary}</div><div className="s">{e.user} · {fmtWhen(e.ts)}</div></div></li>)}</div>
      </Section>

      {edit && (
        <div className="row wrap">
          <button className="btn" onClick={() => setSheet("edit")}><Pencil size={18} /> Edit</button>
          {ct.temporarily_moved && <button className="btn" onClick={() => run(() => api.post(`containers/${ct.id}/return`), "Returned").then(refresh)}><Home size={18} /> Put back</button>}
          <Link className="btn" to={`/labels?ids=${ct.id}`}><Printer size={18} /> Print label</Link>
          <label className="btn"><Camera size={18} /> Add photo<input type="file" accept="image/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { const fd = new FormData(); fd.append("file", f); fd.append("kind", "photo"); fd.append("container_id", String(ct.id)); await run(() => api.upload("attachments", fd), "Photo added"); refresh(); } }} /></label>
          <span className="spacer" />
          <button className="btn danger" disabled={busy} onClick={async () => {
            if (!window.confirm(`Delete ${ct.name}? Anything inside will be moved to where the container is. It can be restored from Trash.`)) return;
            await run(() => api.del(`containers/${ct.id}`), "Deleted");
            nav("/containers");
          }}><Trash2 size={18} /> Delete</button>
        </div>
      )}

      {sheet === "edit" && <ContainerFormSheet container={ct} onClose={() => setSheet(null)} onSaved={() => { setSheet(null); refresh(); }} />}
      {sheet === "move" && <MoveContainerSheet c={ct} onClose={() => setSheet(null)} onDone={() => { setSheet(null); refresh(); }} />}
      {sheet === "qr" && <QrSheet c={ct} onClose={() => setSheet(null)} />}
      {sheet === "scan" && scan && <ScanReview scan={scan} container={ct} onClose={() => { setSheet(null); setScan(null); refresh(); }} />}
    </div>
  );
}

function MoveContainerSheet({ c, onClose, onDone }: { c: Container; onClose: () => void; onDone: () => void }) {
  const [target, setTarget] = useState<PlacementValue | null>(null);
  const [picking, setPicking] = useState(true);
  const [temporary, setTemporary] = useState(false);
  const { busy, run } = useAction();
  return (
    <Sheet title={`Move ${c.name}`} onClose={onClose}>
      <div className="stack">
        <p className="muted">Everything inside moves with it.</p>
        <button className="btn block" style={{ justifyContent: "flex-start" }} onClick={() => setPicking(true)}>{target?.label || "Choose destination…"}</button>
        <label className="check"><input type="checkbox" checked={temporary} onChange={(e) => setTemporary(e.target.checked)} /> Temporary move</label>
        <button className="btn primary block" disabled={!target || busy} onClick={async () => {
          if (await run(() => api.post(`containers/${c.id}/move`, { location_id: target!.location_id, parent_container_id: target!.container_id, temporary }), "Moved")) onDone();
        }}>Move</button>
      </div>
      {picking && <PlacementPicker title="Move to…" excludeContainerId={c.id} onClose={() => setPicking(false)} onPick={(p) => { setTarget(p); setPicking(false); }} />}
    </Sheet>
  );
}

function QrSheet({ c, onClose }: { c: Container; onClose: () => void }) {
  return (
    <Sheet title="QR label" onClose={onClose}>
      <div className="stack" style={{ alignItems: "center" }}>
        <img src={qrImageUrl("containers", c.id)} alt={`QR code ${c.qr_code}`} style={{ width: 240, background: "#fff", borderRadius: 12, padding: 8 }} />
        <div className="mono" style={{ fontSize: "1.2rem", fontWeight: 700 }}>{c.qr_code}</div>
        <p className="muted" style={{ textAlign: "center" }}>Scan it with your phone's camera to open this container's contents. The code is permanent: it keeps working if you rename, renumber or move the container.</p>
        <Link className="btn primary block" to={`/labels?ids=${c.id}`}><Printer size={18} /> Print label</Link>
      </div>
    </Sheet>
  );
}

type Row = { key: number; action: "add" | "increase" | "skip"; name: string; category: string; manufacturer: string; model_number: string; quantity: number; confidence: number | null; existing_item_id: number | null; match: string | null };

export function ScanReview({ scan, container, onClose }: { scan: Scan; container: Container; onClose: () => void }) {
  const [rows, setRows] = useState<Row[]>(() =>
    scan.suggestions.map((s, i) => ({
      key: i, action: s.proposed_action, name: s.name, category: s.category || "", manufacturer: s.manufacturer || "", model_number: s.model_number || "",
      quantity: s.quantity || 1, confidence: s.confidence, existing_item_id: s.match?.item_id || null, match: s.match ? `${s.match.name} (have ${s.match.quantity})` : null,
    })),
  );
  const [removeIds, setRemoveIds] = useState<number[]>([]);
  const { busy, run } = useAction();
  if (scan.status === "analysing" || scan.status === "pending") {
    return <Sheet title="Analysing photo…" onClose={onClose}><div className="center"><div className="spinner" /></div><p className="muted" style={{ textAlign: "center" }}>Looking for items in {container.name}. This can take up to a minute.</p></Sheet>;
  }
  if (scan.status === "failed") {
    return (
      <Sheet title="Photo inventory" onClose={onClose}>
        <div className="error">{scan.error}</div>
        <p>The photo was kept with the container. You can retry or add items manually.</p>
        <div className="row"><button className="btn primary" onClick={() => run(() => api.post(`scans/${scan.id}/retry`)).then(onClose)} disabled={busy}>Retry</button><Link className="btn" to={`/add?container_id=${container.id}`}>Add manually</Link></div>
      </Sheet>
    );
  }
  const matchedIds = new Set(rows.filter((r) => r.existing_item_id).map((r) => r.existing_item_id));
  const notSeen = (container.items || []).filter((i) => !matchedIds.has(i.id));
  const upd = (k: number, patch: Partial<Row>) => setRows(rows.map((r) => (r.key === k ? { ...r, ...patch } : r)));
  const conf = (c: number | null) => (c === null ? "unknown" : c >= 0.8 ? "high" : c >= 0.5 ? "medium" : "low");
  return (
    <Sheet title={`Review: ${container.name}`} onClose={onClose}>
      {scan.photo && <img src={url(scan.photo.thumb_url || scan.photo.url)} alt="" style={{ borderRadius: 12, marginBottom: 12, maxHeight: 220, objectFit: "cover", width: "100%" }} />}
      <p className="muted">Check each suggestion. Nothing is saved until you confirm.</p>
      <div className="stack">
        {rows.map((r) => (
          <div key={r.key} className="card" style={{ marginBottom: 0, opacity: r.action === "skip" ? 0.55 : 1 }}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <input value={r.name} onChange={(e) => upd(r.key, { name: e.target.value })} style={{ fontWeight: 600 }} />
              <button className="iconbtn" onClick={() => upd(r.key, { action: r.action === "skip" ? (r.existing_item_id ? "increase" : "add") : "skip" })} aria-label={r.action === "skip" ? "Include" : "Remove suggestion"}>{r.action === "skip" ? <Plus size={18} /> : <Trash2 size={18} />}</button>
            </div>
            <div className="row wrap" style={{ marginTop: 8 }}>
              <Stepper value={r.quantity} min={1} onChange={(n) => upd(r.key, { quantity: n })} />
              <span className={`badge conf-${conf(r.confidence)}`}>{conf(r.confidence)} confidence</span>
              {r.match && (
                <select value={r.action} onChange={(e) => upd(r.key, { action: e.target.value as Row["action"] })} style={{ width: "auto" }}>
                  <option value="increase">Update existing quantity</option>
                  <option value="add">Add as new item</option>
                  <option value="skip">Already recorded — skip</option>
                </select>
              )}
            </div>
            {r.match && <div className="muted" style={{ fontSize: ".85rem", marginTop: 6 }}>Matches: {r.match}</div>}
          </div>
        ))}
        <button className="btn" onClick={() => setRows([...rows, { key: Date.now(), action: "add", name: "", category: "", manufacturer: "", model_number: "", quantity: 1, confidence: null, existing_item_id: null, match: null }])}><Plus size={18} /> Add something it missed</button>
        {notSeen.length > 0 && (
          <div className="card" style={{ marginBottom: 0 }}>
            <h3>Not seen in this photo</h3>
            <p className="muted">Tick anything that is no longer in the container.</p>
            {notSeen.map((i) => (
              <label key={i.id} className="check" style={{ padding: "6px 0" }}>
                <input type="checkbox" checked={removeIds.includes(i.id)} onChange={(e) => setRemoveIds(e.target.checked ? [...removeIds, i.id] : removeIds.filter((x) => x !== i.id))} />
                {i.quantity} × {i.display_name}
              </label>
            ))}
          </div>
        )}
        <button className="btn primary block big" disabled={busy} onClick={async () => {
          const items = rows.filter((r) => r.name.trim() || r.action === "increase").map((r) => ({
            action: r.action, existing_item_id: r.action === "increase" ? r.existing_item_id : null, name: r.name.trim() || null,
            category: r.category || null, manufacturer: r.manufacturer || null, model_number: r.model_number || null, quantity: r.quantity, confidence: r.confidence,
          }));
          const res = await run(() => api.post<{ created: number[]; increased: number[]; removed: number[] }>(`scans/${scan.id}/confirm`, { items, remove_item_ids: removeIds }));
          if (res) onClose();
        }}>Confirm inventory</button>
        <button className="btn ghost block" onClick={() => run(() => api.post(`scans/${scan.id}/discard`)).then(onClose)}>Discard suggestions</button>
      </div>
    </Sheet>
  );
}
