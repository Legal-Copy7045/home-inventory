import { useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Camera, Check, Move, Pencil, Plus, Trash2 } from "lucide-react";
import { api, uploadPhoto, url, type FlatLocation, type LocationMap, type MapMarker } from "../api";
import { PlacementPicker } from "../components/PlacementPicker";
import { ErrorBox, Empty, Sheet, Spinner, useAction, useApi, useMe } from "../components/ui";

export function MapsPage() {
  const me = useMe();
  const [params] = useSearchParams();
  const location = params.get("location");
  const maps = useApi(() => api.get<LocationMap[]>("maps", { location_id: location }), [location]);
  const [creating, setCreating] = useState(false);
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="row between"><h1>Visual maps</h1>{me.can_edit && <button className="btn primary" onClick={() => setCreating(true)}><Plus size={18} /> New map</button>}</div>
      <p className="muted">Photograph a storage area — shelving, a garage wall, the loft — and pin locations and containers on it.</p>
      {maps.error && !maps.data ? <ErrorBox error={maps.error} retry={maps.reload} /> : maps.loading && !maps.data ? <Spinner /> : maps.data?.length ? (
        <div className="grid two">
          {maps.data.map((m) => (
            <Link key={m.id} to={`/maps/${m.id}`} className="card tight" style={{ color: "inherit" }}>
              <img src={url(m.photo.thumb_url || m.photo.url)} alt="" style={{ width: "100%", aspectRatio: "16/10", objectFit: "cover" }} />
              <div style={{ padding: 12 }}><div style={{ fontWeight: 700 }}>{m.title}</div><div className="muted">{m.location_path || "No location"} · {m.marker_count} marker{m.marker_count === 1 ? "" : "s"}</div></div>
            </Link>
          ))}
        </div>
      ) : <div className="card"><Empty>No maps yet.</Empty></div>}
      {creating && <NewMapSheet locationId={location ? Number(location) : null} onClose={() => setCreating(false)} onSaved={(id) => nav(`/maps/${id}?edit=1`)} />}
    </div>
  );
}

function NewMapSheet({ locationId, onClose, onSaved }: { locationId: number | null; onClose: () => void; onSaved: (id: number) => void }) {
  const [title, setTitle] = useState("");
  const [loc, setLoc] = useState<number | "">(locationId ?? "");
  const [file, setFile] = useState<File | null>(null);
  const locs = useApi(() => api.get<FlatLocation[]>("locations"), []);
  const { busy, run } = useAction();
  return (
    <Sheet title="New visual map" onClose={onClose}>
      <form className="stack" onSubmit={async (e) => {
        e.preventDefault();
        if (!file) return;
        const r = await run(async () => {
          const photo = await uploadPhoto(file, { location_id: loc || undefined });
          return api.post<LocationMap>("maps", { title, location_id: loc || null, photo_id: photo.id });
        }, "Map created");
        if (r) onSaved(r.id);
      }}>
        <label className="btn big block">
          <Camera size={20} /> {file ? file.name : "Photo of the storage area"}
          <input type="file" accept="image/*" hidden onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </label>
        <label className="field">Title<input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Garage – back wall" /></label>
        <label className="field">Location
          <select value={loc} onChange={(e) => setLoc(e.target.value ? Number(e.target.value) : "")}>
            <option value="">—</option>
            {locs.data?.map((l) => <option key={l.id} value={l.id}>{l.path}</option>)}
          </select>
        </label>
        <button className="btn primary block" disabled={busy || !file}>Create &amp; add markers</button>
      </form>
    </Sheet>
  );
}

export function MapView() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const m = useApi(() => api.get<LocationMap>(`maps/${id}`), [id]);
  const [editing, setEditing] = useState(params.get("edit") === "1");
  const [markers, setMarkers] = useState<MapMarker[] | null>(null);
  const [pending, setPending] = useState<{ x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const imgRef = useRef<HTMLDivElement>(null);
  const { busy, run } = useAction();
  if (m.error && !m.data) return <ErrorBox error={m.error} retry={m.reload} />;
  if (m.loading && !m.data) return <Spinner />;
  if (!m.data) return null;
  const map = m.data;
  const list = markers ?? map.markers ?? [];

  function pos(e: React.PointerEvent | React.MouseEvent) {
    const r = imgRef.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  }

  async function save() {
    const body = { markers: list.map((mk) => ({ x: mk.x, y: mk.y, label: mk.label, location_id: mk.target.type === "location" ? mk.target.id : null, container_id: mk.target.type === "container" ? mk.target.id : null })) };
    if (await run(() => api.put(`maps/${map.id}/markers`, body), "Markers saved")) {
      setEditing(false);
      setMarkers(null);
      m.reload();
    }
  }

  return (
    <div className="page">
      <div className="row between">
        <div><h1 style={{ marginBottom: 2 }}>{map.title}</h1>{map.location_path && <div className="muted">{map.location_path}</div>}</div>
        {me.can_edit && !editing && <button className="btn" onClick={() => { setEditing(true); setMarkers(map.markers || []); }}><Pencil size={18} /> Edit markers</button>}
      </div>
      {editing && <div className="notice" style={{ marginTop: 10 }}>Tap the photo to place a marker. Drag markers to adjust. Tap a marker to remove it.</div>}
      <div
        className="mapwrap"
        ref={imgRef}
        style={{ marginTop: 12, cursor: editing ? "crosshair" : "default" }}
        onClick={(e) => { if (editing && dragging === null && e.target === e.currentTarget.querySelector("img")) setPending(pos(e)); }}
        onPointerMove={(e) => { if (dragging !== null) { const p = pos(e); setMarkers(list.map((mk, i) => (i === dragging ? { ...mk, ...p } : mk))); } }}
        onPointerUp={() => window.setTimeout(() => setDragging(null), 0)}
      >
        <img src={url(map.photo.url)} alt={map.title} draggable={false} />
        {list.map((mk, i) => (
          <button
            key={i}
            className={`marker ${selected === i ? "sel" : ""}`}
            style={{ left: `${mk.x * 100}%`, top: `${mk.y * 100}%` }}
            onPointerDown={(e) => { if (editing) { e.preventDefault(); (e.target as HTMLElement).setPointerCapture?.(e.pointerId); setDragging(i); } }}
            onClick={(e) => {
              e.stopPropagation();
              if (editing) { setSelected(i); return; }
              nav(mk.target.type === "container" ? `/containers/${mk.target.id}` : `/locations/${mk.target.id}`);
            }}
            aria-label={mk.label || mk.target.name}
          >
            <span className={`pin ${mk.target.type === "container" ? "c" : ""}`} />
            <span className="tag">{mk.label || (mk.target.number ? `#${mk.target.number} ${mk.target.name}` : mk.target.name)}</span>
          </button>
        ))}
      </div>
      {editing && (
        <div className="row" style={{ marginTop: 12 }}>
          {selected !== null && <button className="btn danger" onClick={() => { setMarkers(list.filter((_, i) => i !== selected)); setSelected(null); }}><Trash2 size={18} /> Remove marker</button>}
          <span className="spacer" />
          <button className="btn" onClick={() => { setEditing(false); setMarkers(null); setSelected(null); }}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={save}><Check size={18} /> Save</button>
        </div>
      )}
      {!editing && (
        <div className="card tight" style={{ marginTop: 14 }}>
          <div className="list">
            {list.map((mk, i) => (
              <Link key={i} to={mk.target.type === "container" ? `/containers/${mk.target.id}` : `/locations/${mk.target.id}`}>
                <Move size={18} className="muted" />
                <div className="grow"><div className="t">{mk.label || mk.target.name}</div><div className="s">{mk.target.type}{mk.target.deleted ? " (deleted)" : ""}</div></div>
              </Link>
            ))}
            {!list.length && <Empty>No markers yet.</Empty>}
          </div>
        </div>
      )}
      {me.can_edit && !editing && (
        <button className="btn danger" style={{ marginTop: 10 }} onClick={async () => { if (window.confirm("Delete this map?")) { await run(() => api.del(`maps/${map.id}`), "Deleted"); nav("/maps"); } }}><Trash2 size={18} /> Delete map</button>
      )}
      {pending && (
        <PlacementPicker
          title="What is here?"
          onClose={() => setPending(null)}
          onPick={(p) => {
            if (!p.container_id && !p.location_id) { setPending(null); return; }
            const name = p.label.split(" › ").pop() || p.label;
            setMarkers([...list, { x: pending.x, y: pending.y, label: null, target: p.container_id ? { type: "container", id: p.container_id, name } : { type: "location", id: p.location_id!, name } }]);
            setPending(null);
          }}
        />
      )}
    </div>
  );
}
