// Checking a place (inventory verification) and the Needs Attention list.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, BellRing, Check, ClipboardCheck, Minus, Plus, ScanLine, X } from "lucide-react";
import { api, type Item } from "../api";
import { Scanner, type Detection } from "../components/Scanner";
import { Empty, ErrorBox, Sheet, Spinner, Thumb, fmtDate, useAction, useApi, useMe, useToast } from "../components/ui";

type Confidence = { level: "never" | "today" | "recent" | "year" | "old"; label: string; verified_at: string | null; changed_since: boolean };
interface CheckRow {
  id: number; name: string; label: string | null; quantity: number; status: string; where: string; photo_url: string | null;
  confidence: Confidence; result: "present" | "adjusted" | "missing" | "found_here" | null; counted: number | null;
}
interface Check {
  id: number; scope_type: string; scope_id: number; scope_label: string; started_by: string | null; started_at: string;
  finished_at: string | null; expected_count: number;
  summary: { present: number; adjusted: number; missing: number; found_here: number; not_checked: number } | null;
  items?: CheckRow[];
  found_here?: { id: number; name: string; result: string; recorded_place: string | null; note: string | null }[];
  previous?: { id: number; finished_at: string; by: string | null; summary: Check["summary"]; changed: number; new_here: number; no_longer_here: number } | null;
}

const CONF_COLOR: Record<Confidence["level"], string> = { never: "var(--muted)", today: "var(--ok, #2e7d32)", recent: "var(--ok, #2e7d32)", year: "var(--muted)", old: "var(--warn)" };

export function ConfidenceBadge({ c }: { c: Confidence }) {
  return <span className="s" style={{ color: CONF_COLOR[c.level] }}>{c.label}</span>;
}

/** /verify?location=ID (or container=, category=) starts a check; /verify/:id continues one; /verify lists them. */
export function VerifyPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const toast = useToast();
  const started = useRef(false);
  const scope = (["location", "container", "category"] as const).find((k) => params.get(k));
  useEffect(() => {
    if (id || !scope || started.current) return;
    started.current = true;
    api.post<Check>("verifications", { [`${scope}_id`]: Number(params.get(scope)) })
      .then((c) => nav(`/verify/${c.id}`, { replace: true }))
      .catch((e) => toast((e as Error).message, true));
  }, [id, scope, params, nav, toast]);
  if (id) return <CheckPage id={Number(id)} />;
  if (scope) return <Spinner />;
  return <ChecksHome />;
}

function ChecksHome() {
  const me = useMe();
  const hist = useApi(() => api.get<{ checks: Check[] }>("verifications"), []);
  const due = useApi(() => api.get<{ places: { type: string; id: number; path: string; things: number; days: number | null }[] }>("checkups", { limit: 8 }), []);
  const cats = useApi(() => api.get<{ id: number; name: string; item_count: number }[]>("categories"), []);
  return (
    <div className="page">
      <h1><ClipboardCheck size={22} style={{ verticalAlign: "-3px" }} /> Check inventory</h1>
      <p className="muted">Walk round a place, box or category and confirm what's really there: tick things, scan barcodes, correct counts, mark what's missing, and note what turned up from elsewhere. Things you don't check stay exactly as they were.</p>
      {me.can_edit && (
        <>
          <h3>Places to check</h3>
          <div className="list card tight">
            {due.error && !due.data ? <ErrorBox error={due.error} retry={due.reload} /> : !due.data ? <Spinner /> : due.data.places.length ? due.data.places.map((p) => (
              <Link key={`${p.type}${p.id}`} to={`/verify?${p.type}=${p.id}`}>
                <div className="grow"><div className="t">{p.path}</div><div className="s">{p.things} thing{p.things === 1 ? "" : "s"} · {p.days === null ? "never checked" : `last checked ${p.days} days ago`}</div></div>
              </Link>
            )) : <Empty>Nothing to suggest.</Empty>}
          </div>
          {cats.data && cats.data.length > 0 && (
            <label className="field" style={{ marginTop: 10 }}>Or check a category
              <select defaultValue="" onChange={(e) => { if (e.target.value) window.location.hash = `#/verify?category=${e.target.value}`; }}>
                <option value="" disabled>Choose…</option>{cats.data.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.item_count})</option>)}
              </select>
            </label>
          )}
        </>
      )}
      <h3>Recent checks</h3>
      <div className="list card tight">
        {hist.error && !hist.data ? <ErrorBox error={hist.error} retry={hist.reload} /> : !hist.data ? <Spinner /> : hist.data.checks.length ? hist.data.checks.map((c) => (
          <Link key={c.id} to={`/verify/${c.id}`}>
            <div className="grow">
              <div className="t">{c.scope_label}</div>
              <div className="s">{fmtDate(c.started_at)}{c.started_by ? ` · ${c.started_by}` : ""} · {c.finished_at ? summaryText(c.summary) : "not finished"}</div>
            </div>
          </Link>
        )) : <Empty>No checks yet. Open a place or box and tap Check this place.</Empty>}
      </div>
    </div>
  );
}

function summaryText(s: Check["summary"]) {
  if (!s) return "";
  const bits = [`${s.present + s.adjusted} here`];
  if (s.adjusted) bits.push(`${s.adjusted} recounted`);
  if (s.missing) bits.push(`${s.missing} missing`);
  if (s.found_here) bits.push(`${s.found_here} found from elsewhere`);
  if (s.not_checked) bits.push(`${s.not_checked} not checked`);
  return bits.join(", ");
}

function CheckPage({ id }: { id: number }) {
  const me = useMe();
  const edit = me.can_edit;
  const toast = useToast();
  const c = useApi(() => api.get<Check>(`verifications/${id}`), [id]);
  const { busy, run } = useAction();
  const [scanning, setScanning] = useState(false);
  const [counting, setCounting] = useState<CheckRow | null>(null);
  const [elsewhere, setElsewhere] = useState<Item[] | null>(null);
  if (c.error && !c.data) return <div className="page"><ErrorBox error={c.error} retry={c.reload} /></div>;
  if (!c.data) return <Spinner />;
  const d = c.data;
  const open = !d.finished_at && edit;
  const set = (data: Check) => c.setData(data);
  const mark = async (row: CheckRow, result: "present" | "adjusted" | "missing", quantity?: number) => {
    const r = await run(() => api.post<Check>(`verifications/${id}/items/${row.id}`, { result, quantity }));
    if (r) set(r);
  };
  const onScan = async (det: Detection) => {
    try {
      const r = await api.post<{ expected: Item[]; elsewhere: Item[]; unknown: boolean; name?: string }>(`verifications/${id}/scan`, { code: det.text, format: det.format });
      if (r.unknown) { toast("That barcode isn't in your inventory", true); return; }
      const mine = r.expected.find((i) => !d.items?.find((x) => x.id === i.id)?.result) || r.expected[0];
      if (mine) {
        const row = d.items?.find((x) => x.id === mine.id);
        if (row) { await mark(row, "present"); toast(`${row.name}: here`); }
        return;
      }
      setElsewhere(r.elsewhere);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const foundHere = async (it: Item, move: boolean) => {
    const r = await run(() => api.post<Check>(`verifications/${id}/found-here`, { item_id: it.id, move }), move ? "Moved here" : "Noted");
    if (r) { set(r); setElsewhere(null); }
  };
  const finish = async () => {
    const left = (d.items || []).filter((i) => !i.result).length;
    if (left && !window.confirm(`${left} thing${left === 1 ? " hasn't" : "s haven't"} been checked. They stay as they are (not marked missing). Finish?`)) return;
    const r = await run(() => api.post<Check>(`verifications/${id}/finish`), "Check finished");
    if (r) set(r);
  };
  const done = (d.items || []).filter((i) => i.result).length;
  return (
    <div className="page">
      <h1><ClipboardCheck size={22} style={{ verticalAlign: "-3px" }} /> Check: {d.scope_label}</h1>
      <p className="muted">{d.finished_at ? `Finished ${fmtDate(d.finished_at)}${d.started_by ? ` by ${d.started_by}` : ""}: ${summaryText(d.summary)}.` : `${done} of ${d.items?.length ?? 0} checked. Tick what's here, correct counts, mark what's missing. Anything you don't check stays as it is.`}</p>
      {d.previous && (
        <div className="notice" style={{ marginBottom: 10 }}>
          Last checked {fmtDate(d.previous.finished_at)}{d.previous.by ? ` by ${d.previous.by}` : ""} ({summaryText(d.previous.summary)}).
          {" "}Since then: {d.previous.changed} changed, {d.previous.new_here} new here, {d.previous.no_longer_here} no longer recorded here.
        </div>
      )}
      {open && (
        <div className="row wrap" style={{ gap: 8, marginBottom: 10 }}>
          <button className="btn" onClick={() => setScanning((v) => !v)}><ScanLine size={18} /> {scanning ? "Stop scanning" : "Scan barcodes"}</button>
          <button className="btn primary" disabled={busy} onClick={finish}><Check size={18} /> Finish check</button>
        </div>
      )}
      {open && scanning && <Scanner onDetect={onScan} paused={!!elsewhere || busy} hint="Scan each thing's barcode" />}
      <div className="list card tight">
        {(d.items || []).map((row) => (
          <li key={row.id}>
            <Thumb src={row.photo_url} />
            <div className="grow">
              <div className="t"><Link to={`/items/${row.id}`}>{row.name}{row.label ? ` · ${row.label}` : ""}</Link>
                {row.result && <span className="badge" style={{ marginLeft: 6, background: row.result === "missing" ? "var(--danger)" : "var(--ok, #2e7d32)", color: "#fff" }}>
                  {{ present: "here", adjusted: `counted ${row.counted}`, missing: "missing", found_here: "found" }[row.result]}</span>}
              </div>
              <div className="s">{row.quantity !== 1 ? `${row.quantity} recorded · ` : ""}{row.where}</div>
              <ConfidenceBadge c={row.confidence} />
            </div>
            {open && (
              <div className="row" style={{ gap: 4 }}>
                <button className="iconbtn" aria-label={`${row.name} is here`} disabled={busy} onClick={() => mark(row, "present")}><Check size={20} /></button>
                <button className="iconbtn" aria-label={`Count ${row.name}`} disabled={busy} onClick={() => setCounting(row)}><span style={{ fontWeight: 700 }}>#</span></button>
                <button className="iconbtn" aria-label={`${row.name} is missing`} disabled={busy} onClick={() => mark(row, "missing")}><X size={20} /></button>
              </div>
            )}
          </li>
        ))}
        {!d.items?.length && <Empty>Nothing is recorded here.</Empty>}
      </div>
      {(d.found_here || []).length > 0 && (
        <>
          <h3>Found here from elsewhere</h3>
          <div className="list card tight">{d.found_here!.map((f) => (
            <li key={f.id}><div className="grow"><div className="t"><Link to={`/items/${f.id}`}>{f.name}</Link></div><div className="s">Recorded in {f.recorded_place} · {f.note}</div></div></li>
          ))}</div>
        </>
      )}
      {counting && <CountSheet row={counting} onClose={() => setCounting(null)} onSave={async (q) => { await mark(counting, "adjusted", q); setCounting(null); }} />}
      {elsewhere && (
        <Sheet title="Not expected here" onClose={() => setElsewhere(null)}>
          {elsewhere.length ? (
            <div className="stack">
              <p className="muted" style={{ marginTop: 0 }}>The records put it somewhere else. Is it one of these?</p>
              {elsewhere.map((it) => (
                <div key={it.id} className="card tight" style={{ padding: 10 }}>
                  <b>{it.display_name}</b><div className="s">Recorded in {it.placement.path}</div>
                  <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
                    {d.scope_type !== "category" && <button className="btn small primary" disabled={busy} onClick={() => foundHere(it, true)}>Move its record here</button>}
                    <button className="btn small" disabled={busy} onClick={() => foundHere(it, false)}>Just note it</button>
                  </div>
                </div>
              ))}
            </div>
          ) : <p>That's in your inventory, but nothing recorded here or elsewhere matches it.</p>}
        </Sheet>
      )}
    </div>
  );
}

function CountSheet({ row, onClose, onSave }: { row: CheckRow; onClose: () => void; onSave: (q: number) => void }) {
  const [q, setQ] = useState(row.counted ?? row.quantity);
  return (
    <Sheet title={`How many ${row.name}?`} onClose={onClose}>
      <div className="row" style={{ gap: 12, justifyContent: "center", margin: "12px 0" }}>
        <button className="iconbtn" aria-label="One fewer" onClick={() => setQ(Math.max(0, q - 1))}><Minus size={22} /></button>
        <input type="number" inputMode="numeric" min={0} value={q} onChange={(e) => setQ(Math.max(0, Number(e.target.value) || 0))} style={{ width: 90, textAlign: "center", fontSize: "1.4rem" }} aria-label="Count" />
        <button className="iconbtn" aria-label="One more" onClick={() => setQ(q + 1)}><Plus size={22} /></button>
      </div>
      <p className="muted" style={{ textAlign: "center" }}>Recorded: {row.quantity}. Saving corrects the stock to what you counted.</p>
      <button className="btn primary block" onClick={() => onSave(q)}>Save count</button>
    </Sheet>
  );
}

// ---------------------------------------------------------------- needs attention

interface AttentionGroup { key: string; title: string; severity: "urgent" | "soon" | "info"; count: number; items: { title: string; detail: string; link: string }[]; link: string; hint: string | null }

export function AttentionPage() {
  const a = useApi(() => api.get<{ groups: AttentionGroup[]; total: number; urgent: number; soon: number }>("attention"), []);
  const [show, setShow] = useState<"all" | "urgent" | "soon" | "info">("all");
  const color = { urgent: "var(--danger)", soon: "var(--warn)", info: "var(--muted)" };
  return (
    <div className="page">
      <h1><BellRing size={22} style={{ verticalAlign: "-3px" }} /> Needs attention</h1>
      {a.error && !a.data ? <ErrorBox error={a.error} retry={a.reload} /> : !a.data ? <Spinner /> : (
        <>
          <p className="muted">{a.data.urgent} urgent, {a.data.soon} soon, {a.data.total - a.data.urgent - a.data.soon} to look at when you can.</p>
          <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
            {(["all", "urgent", "soon", "info"] as const).map((k) => <button key={k} role="tab" aria-selected={show === k} className={show === k ? "active" : ""} onClick={() => setShow(k)}>{{ all: "All", urgent: "Urgent", soon: "Soon", info: "When you can" }[k]}</button>)}
          </div>
          {!a.data.groups.length && <div className="card"><Empty>Nothing needs attention. Everything is up to date.</Empty></div>}
          {a.data.groups.filter((g) => show === "all" || g.severity === show).map((g) => (
            <div key={g.key} className="card" style={{ marginBottom: 10, borderLeft: `4px solid ${color[g.severity]}` }}>
              <div className="row between wrap">
                <b>{g.severity === "urgent" && <AlertTriangle size={14} color="var(--danger)" style={{ verticalAlign: "-2px" }} />} {g.title} <span className="badge">{g.count}</span></b>
                <Link className="btn small ghost" to={g.link}>Open</Link>
              </div>
              {g.hint && <div className="s">{g.hint}</div>}
              {g.items.length > 0 && (
                <ul className="list" style={{ marginTop: 6 }}>
                  {g.items.map((it, n) => <li key={n}><Link to={it.link} className="grow" style={{ display: "block" }}><div className="t">{it.title}</div><div className="s">{it.detail}</div></Link></li>)}
                </ul>
              )}
              {g.count > g.items.length && g.items.length > 0 && <div className="s"><Link to={g.link}>and {g.count - g.items.length} more</Link></div>}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
