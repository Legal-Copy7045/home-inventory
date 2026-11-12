import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Copy, ExternalLink, Nfc, QrCode, RefreshCw, Smartphone } from "lucide-react";
import { api, appBaseUrl, url, type Item } from "../api";
import { Empty, ErrorBox, Section, Sheet, Spinner, fmtWhen, useAction, useApi, useMe, useToast } from "../components/ui";

export type TagTarget = "item" | "container" | "location" | "kit";

export interface TagRow {
  id: number;
  ref: string;
  url: string;
  target_type: TagTarget;
  target_id: number;
  target_name: string | null;
  label: string | null;
  status: "active" | "inactive" | "lost";
  hardware_id: string | null;
  default_action: string;
  actions: string[];
  last_scanned_at: string | null;
  scan_count: number;
}

export const ACTION_LABELS: Record<string, string> = {
  view: "Open it",
  move: "Move",
  adjust: "Use one",
  service: "Log service",
  meter: "Log hours",
  repair: "Start repair job",
  return: "Mark returned",
  scan: "Scan mode",
};

// ---------------------------------------------------------------- Web NFC support

/* Web NFC: Chrome on Android only, secure context, top-level page only (not
   inside Home Assistant's frame). iOS Safari has no Web NFC, but iPhones read
   URL tags natively, so tags always hold a plain link. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type NDEFReaderT = any;
export function nfcSupport(): "yes" | "frame" | "no" {
  const has = typeof window !== "undefined" && "NDEFReader" in window;
  if (!has) return "no";
  return window.top === window ? "yes" : "frame";
}
function newReader(): NDEFReaderT {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return new (window as any).NDEFReader();
}

/** Read one tag: its serial number and any URL record on it. */
export function readNfcOnce(signal: AbortSignal): Promise<{ serial: string | null; url: string | null }> {
  return new Promise((resolve, reject) => {
    const reader = newReader();
    reader.onreading = (ev: { serialNumber?: string; message: { records: { recordType: string; data?: DataView }[] } }) => {
      let link: string | null = null;
      for (const r of ev.message.records || []) {
        if ((r.recordType === "url" || r.recordType === "absolute-url") && r.data) link = new TextDecoder().decode(r.data);
      }
      resolve({ serial: ev.serialNumber || null, url: link });
    };
    reader.onreadingerror = () => reject(new Error("Couldn't read that tag. Try holding it still against the back of the phone."));
    reader.scan({ signal }).catch(reject);
  });
}

function openTopLevel() {
  // The App's own address works on its own while the Home Assistant session is valid.
  window.open(window.location.href, "_blank", "noopener");
}

// ---------------------------------------------------------------- scan landing page

export function TagScanPage() {
  const { ref } = useParams();
  const nav = useNavigate();
  const me = useMe();
  const toast = useToast();
  const [tag, setTag] = useState<TagRow | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();

  useEffect(() => {
    let cancelled = false;
    api.post<TagRow>("tags/resolve", { text: ref }).then(async (t) => {
      if (cancelled) return;
      if (t.default_action === "view" || !me.can_edit) return nav(targetPath(t), { replace: true });
      if (t.default_action === "scan") return nav(`/scan?container=${t.target_id}`, { replace: true });
      setTag(t);
      if (t.target_type === "item") setItem(await api.get<Item>(`items/${t.target_id}`));
    }).catch((e) => setError((e as Error).message));
    return () => { cancelled = true; };
  }, [ref, nav, me.can_edit]);

  if (error) return <div className="page"><ErrorBox error={error} /><Link to="/scan">Scan something else</Link></div>;
  if (!tag) return <Spinner />;
  const go = (action: string) => {
    if (action === "view") return nav(targetPath(tag));
    if (action === "scan") return nav(`/scan?container=${tag.target_id}`);
    if (action === "adjust" && item) {
      return run(() => api.post(`items/${item.id}/adjust`, { delta: -1 }), `Used one – ${Math.max(0, item.quantity - 1)} left`).then(() => nav(`/items/${item.id}`));
    }
    if (action === "return" && item?.loan) {
      return run(() => api.post(`loans/${item.loan!.id}/return`, {}), "Marked as returned").then(() => nav(`/items/${item.id}`));
    }
    if (action === "return") return toast("This item isn't on loan", true);
    return nav(`/items/${tag.target_id}?do=${action}`);
  };
  const actions = tag.actions.filter((a) => (a !== "repair" || item?.owner) && (a !== "return" || item?.loan));
  const ordered = [tag.default_action, ...actions.filter((a) => a !== tag.default_action)].filter((a) => actions.includes(a));
  return (
    <div className="page">
      <div className="row" style={{ gap: 8, marginBottom: 6 }}><Nfc size={20} /><span className="muted">NFC tag{tag.label ? ` · ${tag.label}` : ""}</span></div>
      <h1 style={{ margin: "0 0 14px" }}>{tag.target_name}</h1>
      <div className="stack">
        {ordered.map((a, i) => (
          <button key={a} className={`btn ${i === 0 ? "primary" : ""} block`} style={{ minHeight: 56, fontSize: "1.1rem" }} disabled={busy} onClick={() => go(a)}>
            {ACTION_LABELS[a] || a}
          </button>
        ))}
      </div>
    </div>
  );
}

export function targetPath(t: { target_type: TagTarget; target_id: number }) {
  return { item: `/items/${t.target_id}`, container: `/containers/${t.target_id}`, location: `/locations/${t.target_id}`, kit: `/kits/${t.target_id}` }[t.target_type];
}

// ---------------------------------------------------------------- tags on a record page

export function TagsSection({ targetType, targetId, name }: { targetType: TagTarget; targetId: number; name: string }) {
  const me = useMe();
  const base = appBaseUrl();
  const tags = useApi(() => api.get<TagRow[]>("tags", { target_type: targetType, target_id: targetId, base: base || undefined }), [targetType, targetId]);
  const [assigning, setAssigning] = useState(false);
  const active = (tags.data || []).filter((t) => t.status === "active");
  if (!me.can_edit && !active.length) return null;
  return (
    <Section title="NFC tags" action={me.can_edit && <button className="btn small" onClick={() => setAssigning(true)}><Nfc size={16} /> Assign tag</button>}>
      {active.length ? (
        <ul className="list">
          {active.map((t) => (
            <li key={t.id}>
              <Nfc size={18} />
              <div className="grow">
                <div className="t">{t.label || "NFC tag"}</div>
                <div className="s">Opens: {ACTION_LABELS[t.default_action]}{t.last_scanned_at ? ` · last scanned ${fmtWhen(t.last_scanned_at)}` : " · not scanned yet"}</div>
              </div>
              <Link className="btn small" to="/tags">Manage</Link>
            </li>
          ))}
        </ul>
      ) : <p className="muted" style={{ margin: 0 }}>Stick an NFC tag on it and scan it with your phone to open this page, or to jump straight to an action.</p>}
      {assigning && <AssignTagSheet targetType={targetType} targetId={targetId} name={name} onClose={() => setAssigning(false)} onDone={() => { setAssigning(false); tags.reload(); }} />}
    </Section>
  );
}

function AssignTagSheet({ targetType, targetId, name, onClose, onDone }: { targetType: TagTarget; targetId: number; name: string; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const support = nfcSupport();
  const actionsFor: Record<TagTarget, string[]> = {
    item: ["view", "move", "adjust", "service", "meter", "repair", "return"], container: ["view", "scan"], location: ["view"], kit: ["view"],
  };
  const [label, setLabel] = useState("");
  const [action, setAction] = useState("view");
  const [step, setStep] = useState<"choose" | "nfc" | "link" | "done">("choose");
  const [tag, setTag] = useState<TagRow | null>(null);
  const [status, setStatus] = useState("");
  const { busy, run } = useAction();
  const base = appBaseUrl();

  const create = (hardware_id: string | null) =>
    api.post<TagRow>(`tags${base ? `?base=${encodeURIComponent(base)}` : ""}`, { target_type: targetType, target_id: targetId, label: label || null, default_action: action, hardware_id });

  const writeWithPhone = async () => {
    setStep("nfc");
    const ctl = new AbortController();
    try {
      setStatus("Hold the tag against the back of your phone…");
      const { serial } = await readNfcOnce(ctl.signal);
      ctl.abort();
      if (serial) {
        const { duplicate } = await api.get<{ duplicate: TagRow | null }>("tags/check", { hardware_id: serial });
        if (duplicate) throw new Error(`This tag is already used for “${duplicate.target_name}”. Reassign or deactivate it on the Tags page first.`);
      }
      const t = await create(serial);
      setStatus("Writing… keep holding the tag");
      await newReader().write({ records: [{ recordType: "url", data: t.url }] });
      setTag(t);
      setStep("done");
    } catch (e) {
      ctl.abort();
      setStatus("");
      setStep("choose");
      toast((e as Error).message || "NFC failed", true);
    }
  };
  const makeLink = async () => {
    const t = await run(() => create(null));
    if (t) { setTag(t); setStep("link"); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(tag!.url); toast("Link copied"); } catch { toast("Copy failed – select the link and copy it", true); }
  };
  return (
    <Sheet title={`NFC tag for ${name}`} onClose={step === "done" || step === "link" ? onDone : onClose}>
      {step === "choose" && (
        <div className="stack">
          <label className="field">Label (optional)<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Handle, Lid" /></label>
          <div className="stack" style={{ gap: 6 }}>
            <b style={{ fontSize: ".9rem" }}>When scanned</b>
            <div className="steps">
              {actionsFor[targetType].map((a) => <button key={a} type="button" className={action === a ? "active" : ""} onClick={() => setAction(a)}>{ACTION_LABELS[a]}</button>)}
            </div>
          </div>
          {support === "yes" && <button className="btn primary block" disabled={busy} onClick={writeWithPhone}><Smartphone size={18} /> Write tag with this phone</button>}
          {support === "frame" && (
            <div className="notice">
              Writing NFC tags works only when the app is open on its own, not inside Home Assistant.
              <button className="btn small" style={{ marginTop: 8 }} onClick={openTopLevel}><ExternalLink size={16} /> Open the app in its own tab</button>
            </div>
          )}
          <button className={`btn ${support === "yes" ? "" : "primary"} block`} disabled={busy} onClick={makeLink}><Copy size={18} /> Get the link to write with an NFC app</button>
          {support === "no" && <p className="muted" style={{ margin: 0, fontSize: ".85rem" }}>On iPhone, write the link with an app such as <b>NFC Tools</b> (Write → Add a record → URL). iPhones open link tags without any app.</p>}
        </div>
      )}
      {step === "nfc" && <div className="stack" style={{ alignItems: "center", textAlign: "center" }}><Nfc size={48} /><p>{status}</p></div>}
      {step === "done" && tag && (
        <div className="stack">
          <div className="notice" style={{ background: "var(--ok-soft)", color: "var(--ok)" }}>Tag written and assigned. Scan it to try it.</div>
          <button className="btn primary block" onClick={onDone}>Done</button>
        </div>
      )}
      {step === "link" && tag && (
        <div className="stack">
          <p style={{ marginTop: 0 }}>Write this link to the tag as a <b>URL record</b>:</p>
          <input readOnly value={tag.url} onFocus={(e) => e.target.select()} />
          <button className="btn block" onClick={copy}><Copy size={18} /> Copy link</button>
          <p className="muted" style={{ margin: 0 }}>Or scan this QR code with the phone that writes the tag. You can also print it as a fallback label.</p>
          <img src={url(`api/v1/tags/${tag.id}/qr.svg${base ? `?base=${encodeURIComponent(base)}` : ""}`)} alt="QR code of the tag link" style={{ width: 200, height: 200, alignSelf: "center" }} />
          <button className="btn primary block" onClick={onDone}>Done</button>
        </div>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- management page

export function TagsPage() {
  const me = useMe();
  const base = appBaseUrl();
  const [status, setStatus] = useState<"active" | "inactive" | "lost" | "">("active");
  const tags = useApi(() => api.get<TagRow[]>("tags", { status: status || undefined, base: base || undefined }), [status]);
  const [qr, setQr] = useState<TagRow | null>(null);
  const { busy, run } = useAction();
  const update = async (t: TagRow, body: Record<string, unknown>, msg: string) => {
    if (await run(() => api.patch(`tags/${t.id}`, body), msg)) tags.reload();
  };
  const replace = async (t: TagRow) => {
    if (!window.confirm("Replace this tag? The old one stops working; you'll get a new link to write to a new tag.")) return;
    const n = await run(() => api.post<TagRow>(`tags/${t.id}/replace`, {}), "New tag issued");
    if (n) { tags.reload(); setQr(n); }
  };
  return (
    <div className="page">
      <div className="tabs">
        {(["active", "inactive", "lost", ""] as const).map((s) => <button key={s || "all"} className={status === s ? "active" : ""} onClick={() => setStatus(s)}>{s ? s[0].toUpperCase() + s.slice(1) : "All"}</button>)}
      </div>
      {nfcSupport() !== "no" && <ScanWithPhone />}
      <ErrorBox error={tags.error} />
      {tags.loading && !tags.data ? <Spinner /> : tags.data?.length ? (
        <ul className="list card tight">
          {tags.data.map((t) => (
            <li key={t.id} style={{ flexWrap: "wrap" }}>
              <Nfc size={18} />
              <div className="grow">
                <div className="t">{t.target_name ? <Link to={targetPath(t)}>{t.target_name}</Link> : <span className="muted">Deleted {t.target_type}</span>}{t.label ? ` · ${t.label}` : ""}</div>
                <div className="s">{t.target_type} · {t.status}{t.hardware_id ? ` · chip ${t.hardware_id}` : ""} · scanned {t.scan_count}×</div>
              </div>
              {me.can_edit && (
                <div className="row wrap" style={{ gap: 6 }}>
                  <select value={t.default_action} disabled={busy} onChange={(e) => update(t, { default_action: e.target.value }, "Saved")} aria-label="When scanned">
                    {t.actions.map((a) => <option key={a} value={a}>{ACTION_LABELS[a]}</option>)}
                  </select>
                  <button className="btn small" onClick={() => setQr(t)}><QrCode size={16} /> Link / QR</button>
                  {t.status === "active"
                    ? <button className="btn small" disabled={busy} onClick={() => update(t, { status: "inactive" }, "Tag deactivated")}>Deactivate</button>
                    : <button className="btn small" disabled={busy} onClick={() => update(t, { status: "active" }, "Tag active")}>Reactivate</button>}
                  <button className="btn small" disabled={busy} onClick={() => replace(t)}><RefreshCw size={16} /> Replace</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : <Empty>No {status || ""} tags. Assign tags from an item, container, location or kit page.</Empty>}
      {qr && (
        <Sheet title="Tag link" onClose={() => setQr(null)}>
          <div className="stack">
            <input readOnly value={qr.url} onFocus={(e) => e.target.select()} />
            <img src={url(`api/v1/tags/${qr.id}/qr.svg${base ? `?base=${encodeURIComponent(base)}` : ""}`)} alt="QR code" style={{ width: 220, height: 220, alignSelf: "center" }} />
            <p className="muted" style={{ margin: 0 }}>Print or display this QR code as a fallback; it opens the same thing as the NFC tag.</p>
          </div>
        </Sheet>
      )}
    </div>
  );
}

/** Android top-level only: read a tag in-app (useful to find out what an unlabelled tag is). */
export function ScanWithPhone() {
  const nav = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (nfcSupport() === "frame") {
    return <button className="btn block" style={{ marginBottom: 10 }} onClick={openTopLevel}><Nfc size={18} /> Scan NFC (opens the app in its own tab)</button>;
  }
  const scan = async () => {
    setBusy(true);
    const ctl = new AbortController();
    try {
      const { serial, url: link } = await readNfcOnce(ctl.signal);
      ctl.abort();
      const t = await api.post<TagRow>("tags/resolve", { text: link || "", hardware_id: serial });
      nav(`/tag/${t.ref}`);
    } catch (e) {
      ctl.abort();
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return <button className="btn block" style={{ marginBottom: 10 }} disabled={busy} onClick={scan}><Nfc size={18} /> {busy ? "Hold a tag to the phone…" : "Scan an NFC tag"}</button>;
}
