import { useMemo, useState } from "react";
import { Box, MapPin, Plus } from "lucide-react";
import { api, type Container, type FlatLocation } from "../api";
import { Sheet, Spinner, useApi, useToast } from "./ui";

export interface PlacementValue {
  container_id: number | null;
  location_id: number | null;
  label: string;
}

/**
 * Choose where something is: inside a container, or directly in a location
 * (loose items on shelves, drawers, workbenches...).  Also allows creating
 * a new location on the fly.
 */
export function PlacementPicker({
  title = "Choose where",
  allowContainers = true,
  excludeContainerId,
  onPick,
  onClose,
}: {
  title?: string;
  allowContainers?: boolean;
  excludeContainerId?: number;
  onPick: (v: PlacementValue) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"containers" | "locations">(allowContainers ? "containers" : "locations");
  const locs = useApi(() => api.get<FlatLocation[]>("locations"), []);
  const conts = useApi(() => (allowContainers ? api.get<Container[]>("containers") : Promise.resolve([])), [allowContainers]);
  const toast = useToast();

  const ql = q.trim().toLowerCase();
  const filteredLocs = useMemo(() => (locs.data || []).filter((l) => !ql || l.path.toLowerCase().includes(ql)), [locs.data, ql]);
  const filteredConts = useMemo(
    () =>
      (conts.data || []).filter(
        (c) => c.id !== excludeContainerId && (!ql || `${c.name} ${c.number ?? ""} ${c.path}`.toLowerCase().includes(ql)),
      ),
    [conts.data, ql, excludeContainerId],
  );

  async function createLocation(parentId: number | null) {
    const name = q.trim() || window.prompt("New location name") || "";
    if (!name) return;
    try {
      const loc = await api.post<{ id: number; path: string }>("locations", { name, parent_id: parentId });
      onPick({ location_id: loc.id, container_id: null, label: loc.path });
    } catch (e) {
      toast((e as Error).message, true);
    }
  }

  return (
    <Sheet title={title} onClose={onClose}>
      <input autoFocus placeholder="Search by name, number or place…" value={q} onChange={(e) => setQ(e.target.value)} />
      {allowContainers && (
        <div className="tabs" style={{ marginTop: 10 }}>
          <button className={tab === "containers" ? "active" : ""} onClick={() => setTab("containers")}>
            <Box size={14} /> Containers
          </button>
          <button className={tab === "locations" ? "active" : ""} onClick={() => setTab("locations")}>
            <MapPin size={14} /> Locations (loose)
          </button>
        </div>
      )}
      <div className="card tight" style={{ marginTop: allowContainers ? 0 : 10 }}>
        {(tab === "containers" ? conts.loading : locs.loading) ? (
          <Spinner />
        ) : (
          <div className="list">
            {tab === "containers" &&
              filteredConts.map((c) => (
                <a key={c.id} href="#" onClick={(e) => (e.preventDefault(), onPick({ container_id: c.id, location_id: null, label: c.path }))}>
                  <Box size={20} />
                  <div className="grow">
                    <div className="t">
                      {c.number ? `#${c.number} ` : ""}
                      {c.name}
                    </div>
                    <div className="s">{c.placement?.path || "Unassigned"}</div>
                  </div>
                </a>
              ))}
            {tab === "locations" &&
              filteredLocs.map((l) => (
                <a key={l.id} href="#" onClick={(e) => (e.preventDefault(), onPick({ location_id: l.id, container_id: null, label: l.path }))}>
                  <MapPin size={20} />
                  <div className="grow">
                    <div className="t">{l.name}</div>
                    <div className="s">{l.path}</div>
                  </div>
                </a>
              ))}
            {tab === "containers" && !filteredConts.length && <div className="empty">No containers match.</div>}
            {tab === "locations" && !filteredLocs.length && <div className="empty">No locations match.</div>}
          </div>
        )}
      </div>
      {tab === "locations" && (
        <button className="btn block" onClick={() => createLocation(null)}>
          <Plus size={18} /> New top-level location{q.trim() ? ` "${q.trim()}"` : ""}
        </button>
      )}
      <button className="btn ghost block" style={{ marginTop: 8 }} onClick={() => onPick({ container_id: null, location_id: null, label: "Unassigned" })}>
        Leave unassigned
      </button>
    </Sheet>
  );
}
