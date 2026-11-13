import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { TagsSection } from "./Tags";
import { ArrowRightLeft, Camera, Home, Images, Pencil, Plus, Printer, QrCode, RotateCw, ScanLine, Sparkles, Trash2 } from "lucide-react";
import { api, qrImageUrl, url, type Container, type HistoryEvent, type Scan } from "../api";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { ScanReview, scanPlaceholder } from "../components/PhotoInventory";
import { SelectableItems } from "../components/BulkActions";
import { ContainerRow, Empty, ErrorBox, PathView, Section, Sheet, Spinner, fmtWhen, useAction, useApi, useMe, useToast } from "../components/ui";

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
  const numbers = useApi(() => api.get<{ next: string; used: Record<string, { id: number; name: string; number: string }> }>("containers/numbers"), []);
  // A new container starts with the next free number.
  useEffect(() => {
    if (!container && numbers.data && number === "") setNumber(numbers.data.next);
  }, [numbers.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const clash = (() => {
    const key = number.trim().replace(/^#/, "").toLowerCase();
    const hit = key ? numbers.data?.used[key] : undefined;
    return hit && hit.id !== container?.id ? hit : null;
  })();
  const [type, setType] = useState(container?.container_type || "");
  const [desc, setDesc] = useState(container?.description || "");
  const [place, setPlace] = useState<PlacementValue>(initial || { container_id: null, location_id: null, label: "Unassigned" });
  const [picking, setPicking] = useState(false);
  const { busy, run } = useAction();
  return (
    <Sheet title={container ? "Edit container" : "New container"} onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault();
        if (clash && !window.confirm(`#${clash.number} is already used by “${clash.name}”. Use it again anyway?`)) return;
        const body = { name, number: number || null, container_type: type || null, description: desc || null };
        const r = container
          ? await run(() => api.patch<Container>(`containers/${container.id}`, body), "Saved")
          : await run(() => api.post<Container>("containers", { ...body, location_id: place.location_id, parent_container_id: place.container_id }), "Container created");
        if (r) onSaved(r);
      }}>
        <div className="grid two">
          <label className="field">Name<input required autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Christmas lights" /></label>
          <label className="field">Number<input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="e.g. 15" />
            {clash
              ? <span className="hint" style={{ color: "var(--warn)" }}>Already used by “{clash.name}”{numbers.data ? `; next free is ${numbers.data.next}` : ""}</span>
              : !container && numbers.data && <span className="hint">Next free number: {numbers.data.next}</span>}
          </label>
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
  const [tagReq, setTagReq] = useState(0);
  const [scan, setScan] = useState<Scan | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  if (c.loading && !c.data) return <Spinner />;
  if (!c.data) return <div className="page"><ErrorBox error={c.error} /></div>;
  const ct = c.data;
  const edit = me.can_edit && !ct.deleted_at;
  const refresh = () => { c.reload(); hist.reload(); scans.reload(); };
  const pending = scans.data?.find((s) => s.status === "ready");

  // The newest confirmed photo inventory with its photo: can be looked at again
  // (e.g. so the AI also lists things it couldn't identify the first time).
  const lastPhoto = scans.data?.find((s) => s.status === "confirmed" && s.photo)?.photo || null;
  async function analyseAgain() {
    if (!lastPhoto || !window.confirm("Look at the last photo inventory photo again? Things already recorded are recognised; you review anything new before it's added.")) return;
    setSheet("scan");
    setScan(scanPlaceholder("container", ct.id));
    const f = new FormData();
    f.append("attachment_id", String(lastPhoto.id));
    try {
      setScan(await api.upload<Scan>(`containers/${ct.id}/scans`, f));
      scans.reload();
    } catch (e) {
      toast((e as Error).message, true);
      setSheet(null);
    }
  }

  async function analyse(file: File) {
    setSheet("scan");
    setScan(scanPlaceholder("container", ct.id));
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
            <input type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) analyse(f); }} />
          </label>}
          {me.app.ai.enabled && lastPhoto && (
            <button onClick={analyseAgain}><span className="ico"><RotateCw size={20} /></span>Last photo again</button>
          )}
          <button onClick={() => setSheet("move")}><span className="ico"><ArrowRightLeft size={20} /></span>Move</button>
          <Link to={`/capture?container_id=${ct.id}`}><span className="ico"><Images size={20} /></span>Quick photos</Link>
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
          <>
            {ct.containers?.length ? <div className="list">{ct.containers.map((cc) => <ContainerRow key={`c${cc.id}`} c={cc} />)}</div> : null}
            <SelectableItems bare inPlace items={ct.items || []} onChanged={refresh} />
          </>
        ) : <Empty>Empty. Add items, or photograph the open container and let AI suggest what's inside.</Empty>}
      </Section>

      {ct.photos && ct.photos.length > 0 && (
        <Section title="Photos">
          <div className="photos">{ct.photos.map((p) => <a key={p.id} className="ph" href={url(p.url)} target="_blank" rel="noreferrer"><img src={url(p.thumb_url || p.url)} alt="" /></a>)}</div>
        </Section>
      )}
      {ct.maps && ct.maps.length > 0 && <Section title="Visual maps">{ct.maps.map((m) => <Link key={m.id} className="btn" to={`/maps/${m.id}`}>{m.title}</Link>)}</Section>}

      <TagsSection targetType="container" targetId={ct.id} name={ct.name} hideWhenEmpty request={tagReq} />
      {edit && <div className="add-row"><span className="muted">Add:</span><button className="chip" onClick={() => setTagReq((n) => n + 1)}><Plus size={14} /> NFC tag</button></div>}
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
      {sheet === "scan" && scan && <ScanReview scan={scan} target={{ kind: "container", id: ct.id, name: ct.name, items: ct.items || [] }} onClose={() => { setSheet(null); setScan(null); refresh(); }} />}
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
