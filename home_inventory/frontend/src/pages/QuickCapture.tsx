import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Check, ImagePlus, Layers, Loader2, RotateCcw } from "lucide-react";
import { api, url, type Item } from "../api";
import { discard, enqueue, pending as queued, retry as retryUpload, subscribe } from "../uploadQueue";
import { cameraAvailable } from "../components/Scanner";
import { useApi } from "../components/ui";

type Shot = { key: string; group: number; preview: string; state: "uploading" | "waiting" | "done" | "error"; item?: Item; error?: string };

/**
 * Quick photos: snap item after item; each photo becomes an item flagged
 * "needs details" (finish them later from the To finish list).  Uploads run
 * one at a time in the background so the shutter never waits.
 *
 * Opened from a container or location, photos go straight there.  Opened on
 * its own, photos have no place yet: tap "Next shelf" between shelves (or
 * boxes, drawers...) and say where each group is afterwards on "To sort".
 */
export default function QuickCapture() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const containerId = params.get("container_id");
  const locationId = params.get("location_id");
  const unsorted = !containerId && !locationId;
  const target = useApi(
    () =>
      unsorted
        ? Promise.resolve(null)
        : containerId
          ? api.get<{ path: string; name: string }>(`containers/${containerId}`)
          : api.get<{ path: string; name: string }>(`locations/${locationId}`),
    [containerId, locationId],
  );
  const back = unsorted ? "/sort" : containerId ? `/containers/${containerId}` : `/locations/${locationId}`;
  // One id per visit to this page, so its groups stay together on "To sort".
  const batch = useRef(`${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);
  const [group, setGroup] = useState(1);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [live, setLive] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [shots, setShots] = useState<Shot[]>([]);
  const [flash, setFlash] = useState(false);

  // Photos are saved on the phone first and uploaded by the shared queue,
  // so closing the app or losing signal doesn't lose them.
  useEffect(() => {
    const off = subscribe((e) => {
      if (e.type === "uploading") update(e.id, { state: "uploading", error: undefined });
      else if (e.type === "done") update(e.id, { state: "done", item: e.item });
      else if (e.type === "failed") update(e.id, { state: e.retrying ? "waiting" : "error", error: e.error });
      else if (e.type === "removed") setShots((all) => all.filter((s) => s.key !== e.id));
    });
    // Photos left over from an earlier visit that haven't uploaded yet.
    void queued().then((left) => {
      setShots((all) => {
        const known = new Set(all.map((s) => s.key));
        const extra = left.filter((q) => !known.has(q.id)).map<Shot>((q) => ({
          key: q.id, group: Number(q.fields.group || 1), preview: URL.createObjectURL(q.blob),
          state: q.error ? "error" : "waiting", error: q.error,
        }));
        return [...all, ...extra];
      });
    });
    return off;
  }, []);

  useEffect(() => {
    if (!cameraAvailable()) return;
    let stream: MediaStream | null = null;
    let stopped = false;
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false })
      .then(async (s) => {
        if (stopped) return s.getTracks().forEach((t) => t.stop());
        stream = s;
        const v = videoRef.current!;
        v.srcObject = s;
        v.setAttribute("playsinline", "true");
        await v.play();
        setLive(true);
      })
      .catch((e) => setCamError((e as Error).name === "NotAllowedError" ? "Camera permission was denied." : "Camera unavailable."));
    return () => {
      stopped = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  function update(key: string, patch: Partial<Shot>) {
    setShots((all) => all.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  }

  async function add(blob: Blob) {
    const fields: Record<string, string> = { batch: batch.current, group: String(group) };
    if (containerId) fields.container_id = containerId;
    else if (locationId) fields.location_id = locationId;
    const preview = URL.createObjectURL(blob);
    const entry = await enqueue(blob, fields);
    setShots((all) => (all.some((s) => s.key === entry.id) ? all : [{ key: entry.id, group, preview, state: "uploading" }, ...all]));
  }

  function snap() {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    setFlash(true);
    window.setTimeout(() => setFlash(false), 120);
    if (navigator.vibrate) navigator.vibrate(30);
    c.toBlob((b) => b && add(b), "image/jpeg", 0.9);
  }

  function retry(shot: Shot) {
    update(shot.key, { state: "uploading", error: undefined });
    void retryUpload(shot.key);
  }

  const done = shots.filter((s) => s.state === "done").length;
  const pending = shots.filter((s) => s.state === "uploading" || s.state === "waiting").length;
  const inGroup = shots.filter((s) => s.group === group).length;
  const groups = [...new Set(shots.map((s) => s.group))].sort((a, b) => b - a);

  return (
    <div className="page">
      <div className="scanbar">
        <div className="t">
          {unsorted ? <>Quick photos · Shelf {group}</> : <>Quick photos → {target.data?.name || "…"}</>}
          <div style={{ fontWeight: 500, fontSize: ".8rem", opacity: 0.9 }}>
            {unsorted ? `${inGroup} photo${inGroup === 1 ? "" : "s"} in this group · place them later` : target.data?.path}
          </div>
        </div>
        <button className="btn small" onClick={() => nav(back)} aria-label="Done"><Check size={16} /> Done</button>
      </div>
      {unsorted ? (
        <p className="muted" style={{ marginTop: 0 }}>
          One photo per item. Tap <b>Next shelf</b> when you move on to another shelf, box or drawer. Afterwards, say where each group is on{" "}
          <b>To sort</b>, and add details from <b>To finish</b>.
        </p>
      ) : (
        <p className="muted" style={{ marginTop: 0 }}>One photo per item. Add the details later from <b>To finish</b>{" "}— nothing else to do now.</p>
      )}

      {cameraAvailable() && !camError ? (
        <div className="camera" style={{ aspectRatio: "3/4" }}>
          <video ref={videoRef} muted playsInline />
          {flash && <div style={{ position: "absolute", inset: 0, background: "#fff", opacity: 0.6 }} />}
          {live && (
            <button className="shutter" onClick={snap} aria-label="Take photo">
              <span />
            </button>
          )}
        </div>
      ) : (
        <div className="notice">{camError || "Live camera needs Home Assistant opened over https."} Use the button below to take photos with your camera app instead.</div>
      )}

      <div className="row wrap" style={{ margin: "12px 0" }}>
        {unsorted && (
          <button className="btn primary" style={{ flex: 1 }} disabled={!inGroup} onClick={() => setGroup((g) => g + 1)}>
            <Layers size={18} /> Next shelf
          </button>
        )}
        <label className="btn" style={{ flex: 1 }}>
          <ImagePlus size={18} /> {cameraAvailable() && !camError ? "From gallery" : "Take / choose photos"}
          <input type="file" accept="image/*" multiple hidden
            onChange={(e) => { Array.from(e.target.files || []).forEach(add); e.target.value = ""; }} />
        </label>
      </div>

      <div className="row between">
        <b>{done} added{pending ? ` · ${pending} uploading` : ""}</b>
        {done > 0 && (unsorted ? <Link to="/sort">Say where they are →</Link> : <Link to="/to-finish">Add details now →</Link>)}
      </div>
      {groups.map((g) => (
        <div key={g}>
          {unsorted && <div className="muted" style={{ margin: "12px 0 6px", fontWeight: 600 }}>Shelf {g}</div>}
          <div className="photos" style={{ marginTop: unsorted ? 0 : 8 }}>
            {shots.filter((s) => s.group === g).map((s) => (
          <div key={s.key} className="ph">
            <img src={s.item?.photo?.thumb_url ? url(s.item.photo.thumb_url) : s.preview} alt="" />
            <span className="star">
              {s.state === "uploading" && <span className="badge"><Loader2 size={12} className="spin" /></span>}
              {s.state === "done" && <span className="badge ok"><Check size={12} /></span>}
              {s.state === "waiting" && <span className="badge warn" title={s.error}>Saved · waiting</span>}
              {s.state === "error" && (
                <>
                  <button className="badge danger" style={{ border: 0 }} onClick={() => retry(s)} title={s.error}><RotateCcw size={12} /> Retry</button>{" "}
                  <button className="badge" style={{ border: 0 }} onClick={() => { if (window.confirm("Discard this photo?")) void discard(s.key); }}>Discard</button>
                </>
              )}
            </span>
          </div>
            ))}
          </div>
        </div>
      ))}
      {!shots.length && <div className="empty">No photos yet.</div>}
    </div>
  );
}
