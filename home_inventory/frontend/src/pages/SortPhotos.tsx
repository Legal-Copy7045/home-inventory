import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Camera, Check, MapPin } from "lucide-react";
import { api, url, type Item } from "../api";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { Empty, ErrorBox, Spinner, useApi, useMe, useToast } from "../components/ui";

interface Group {
  batch: string;
  group: number;
  taken_at: string | null;
  items: Item[];
}

function when(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * "To sort": quick photos taken without a place, in the groups they were
 * taken in (one group per shelf).  Choose where a group is and every
 * selected photo in it moves there in one go.
 */
export default function SortPhotos() {
  const me = useMe();
  const toast = useToast();
  const list = useApi(() => api.get<{ total: number; groups: Group[] }>("capture/unsorted"), []);
  // Deselected photos (everything in a group is selected to start with).
  const [off, setOff] = useState<Set<number>>(new Set());
  const [picking, setPicking] = useState<Group | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setOff(new Set()), [list.data]);

  const toggle = (id: number) =>
    setOff((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  async function place(g: Group, v: PlacementValue) {
    setPicking(null);
    if (v.container_id === null && v.location_id === null) return;
    const ids = g.items.filter((i) => !off.has(i.id)).map((i) => i.id);
    setBusy(true);
    try {
      await api.post("capture/place", { item_ids: ids, container_id: v.container_id, location_id: v.location_id });
      toast(`${ids.length} item${ids.length === 1 ? "" : "s"} put in ${v.label}`);
      list.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }

  const groups = list.data?.groups || [];
  let lastBatch = "";
  return (
    <div className="page">
      <div className="row between">
        <h1>To sort</h1>
        {me.can_edit && <Link className="btn small" to="/capture"><Camera size={16} /> Quick photos</Link>}
      </div>
      <p className="muted">
        Quick photos taken without a place, grouped the way you took them. Choose where each group is — untick any photo that's somewhere else first.
      </p>
      <ErrorBox error={list.error} />
      {list.error && !list.data ? <ErrorBox error={list.error} retry={list.reload} /> : list.loading && !list.data ? <Spinner /> : !groups.length ? (
        <div className="card"><Empty>Nothing to sort. Use <b>Quick photos</b> to photograph shelf after shelf, then say where each shelf is here.</Empty></div>
      ) : (
        groups.map((g) => {
          const header = g.batch !== lastBatch ? when(g.taken_at) : null;
          lastBatch = g.batch;
          const chosen = g.items.filter((i) => !off.has(i.id)).length;
          return (
            <div key={`${g.batch}:${g.group}`}>
              {header && <div className="muted" style={{ margin: "16px 0 6px", fontSize: ".85rem" }}>Taken {header}</div>}
              <div className="card">
                <div className="row between" style={{ marginBottom: 8 }}>
                  <b>Shelf {g.group}</b>
                  <span className="muted">{chosen} of {g.items.length} selected</span>
                </div>
                <div className="photos sort-photos">
                  {g.items.map((i) => {
                    const on = !off.has(i.id);
                    return (
                      <button
                        key={i.id}
                        type="button"
                        className={`ph ${on ? "on" : "off"}`}
                        onClick={() => toggle(i.id)}
                        aria-pressed={on}
                        aria-label={`${on ? "Unselect" : "Select"} ${i.capture?.suggestion?.name || "photo"}`}
                        disabled={!me.can_edit}
                      >
                        {i.photo?.thumb_url ? <img src={url(i.photo.thumb_url)} alt="" draggable={false} /> : null}
                        {me.can_edit && <span className="tick">{on && <Check size={14} />}</span>}
                      </button>
                    );
                  })}
                </div>
                {me.can_edit && (
                  <button className="btn primary block" style={{ marginTop: 10 }} disabled={busy || !chosen} onClick={() => setPicking(g)}>
                    <MapPin size={18} /> Put {chosen === g.items.length ? (chosen === 1 ? "it" : `all ${chosen}`) : chosen} in…
                  </button>
                )}
              </div>
            </div>
          );
        })
      )}
      {picking && <PlacementPicker title={`Where is shelf ${picking.group}?`} onPick={(v) => place(picking, v)} onClose={() => setPicking(null)} />}
    </div>
  );
}
