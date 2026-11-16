import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Check, FileUp, Printer } from "lucide-react";
import { api, url, type Container } from "../api";
import { PlacementPicker } from "../components/PlacementPicker";
import { Empty, ErrorBox, Section, Spinner, useAction, useApi, useMe, useToast } from "../components/ui";

type Action = "back" | "move" | "dispose" | "missing" | "none";
interface Row { row: number; item_id: number; name: string; label: string | null; quantity: number; status: string; inside: string | null; photo_id: number | null }
interface ReadRow extends Row { action: Action; quantity_now: number | null; move_to: string | null; place: { type: "location" | "container"; id: number; path: string } | null; note: string | null; confidence: number | null }
interface Decision { action: Action; quantity: string; place: { container_id: number | null; location_id: number | null; label: string } | null; note: string | null; heard?: string | null; confidence?: number | null }
interface ReadResult { pages: number; rows: ReadRow[]; unmatched: { row: number | null; item_id: number | null; action: string }[]; extras: string[] }

const LABEL: Record<Action, string> = { back: "Back here", move: "Move to…", dispose: "Dispose", missing: "Missing", none: "No mark" };

/** Print a box's contents checklist, then read the completed pages back
 * (photos or a scanned PDF), check every row and apply it in one go. */
export function ContainerChecklist() {
  const { id } = useParams();
  const cid = Number(id);
  const me = useMe();
  const nav = useNavigate();
  const toast = useToast();
  const box = useApi(() => api.get<Container>(`containers/${cid}`), [cid]);
  const list = useApi(() => api.get<{ rows: Row[] }>(`containers/${cid}/checklist`), [cid]);
  const [decisions, setDecisions] = useState<Record<number, Decision> | null>(null);
  const [extras, setExtras] = useState<string[]>([]);
  const [unmatched, setUnmatched] = useState(0);
  const [pages, setPages] = useState(0);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState<number | null>(null);
  const { busy, run } = useAction();
  const rows = list.data?.rows || [];

  const blank = (): Record<number, Decision> => Object.fromEntries(rows.map((r) => [r.item_id, { action: "none" as Action, quantity: "", place: null, note: null }]));
  const readFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setReading(true);
    setError(null);
    const next = { ...(decisions || blank()) };
    let more: string[] = [];
    let lost = 0;
    let n = 0;
    try {
      for (const f of Array.from(files)) {
        const form = new FormData();
        form.append("file", f);
        const r = await api.upload<ReadResult>(`containers/${cid}/checklist/read`, form);
        n += r.pages;
        for (const x of r.rows) {
          next[x.item_id] = {
            action: x.action, quantity: x.quantity_now !== null && x.quantity_now !== x.quantity ? String(x.quantity_now) : "",
            place: x.place ? { container_id: x.place.type === "container" ? x.place.id : null, location_id: x.place.type === "location" ? x.place.id : null, label: x.place.path } : null,
            note: x.note, heard: x.move_to, confidence: x.confidence,
          };
        }
        more = [...more, ...r.extras];
        lost += r.unmatched.length;
      }
      setDecisions(next);
      setExtras((e) => [...e, ...more]);
      setUnmatched((u) => u + lost);
      setPages((p) => p + n);
      toast(`Read ${n} page${n === 1 ? "" : "s"}: check each row below`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (Object.keys(next).length) setDecisions(next);
    } finally {
      setReading(false);
    }
  };
  const set = (itemId: number, d: Partial<Decision>) => setDecisions((all) => ({ ...(all || blank()), [itemId]: { ...(all || blank())[itemId], ...d } }));
  const todo = decisions ? rows.filter((r) => decisions[r.item_id] && (decisions[r.item_id].action !== "none" || decisions[r.item_id].quantity !== "")) : [];
  const unresolvedMove = todo.some((r) => decisions![r.item_id].action === "move" && !decisions![r.item_id].place);
  const apply = async () => {
    const actions = todo.map((r) => {
      const d = decisions![r.item_id];
      return { item_id: r.item_id, action: d.action, quantity: d.quantity === "" ? null : Number(d.quantity),
        container_id: d.action === "move" ? d.place?.container_id ?? null : null, location_id: d.action === "move" && !d.place?.container_id ? d.place?.location_id ?? null : null, note: d.note };
    });
    const r = await run(() => api.post<{ back: number; move: number; dispose: number; missing: number; counted: number }>(`containers/${cid}/checklist/apply`, { actions }));
    if (r) {
      toast(`Done: ${r.back} back, ${r.move} moved, ${r.dispose} disposed of, ${r.missing} missing, ${r.counted} recounted`);
      nav(`/containers/${cid}`);
    }
  };

  if (list.error && !list.data) return <div className="page"><ErrorBox error={list.error} retry={list.reload} /></div>;
  if (!list.data) return <Spinner />;
  return (
    <div className="page">
      <h1>Printed checklist</h1>
      <p className="muted">{box.data ? <>For <Link to={`/containers/${cid}`}>{box.data.number ? `${box.data.number} · ` : ""}{box.data.name}</Link>: </> : null}
        print a list of everything in the box with photos and boxes to tick (back here, move to, dispose, missing, new count). Take it to the box, fill it in, then photograph or scan every page here. Nothing changes until you've checked each row and tapped <b>Apply</b>.</p>
      <div className="row wrap" style={{ gap: 8 }}>
        <a className="btn" href={url(`api/v1/containers/${cid}/checklist.pdf`)} target="_blank" rel="noreferrer"><Printer size={18} /> Print checklist ({rows.length} item{rows.length === 1 ? "" : "s"})</a>
        {me.can_edit && me.app.ai.enabled && (
          <label className="btn primary" style={{ cursor: "pointer" }}>
            <FileUp size={18} /> {reading ? "Reading…" : pages ? "Read more pages" : "Read completed checklist"}
            <input type="file" accept="image/*,application/pdf" multiple hidden disabled={reading} onChange={(e) => { const f = e.target.files; readFiles(f); e.target.value = ""; }} />
          </label>
        )}
        {me.can_edit && !decisions && <button className="btn ghost" onClick={() => setDecisions(blank())}>Fill in by hand</button>}
      </div>
      {!me.app.ai.enabled && me.can_edit && <p className="muted">AI isn't set up, so completed pages can't be read automatically: use <b>Fill in by hand</b>.</p>}
      <ErrorBox error={error} />
      {reading && <Spinner />}
      {decisions && (
        <Section title={`Check each row${pages ? ` (${pages} page${pages === 1 ? "" : "s"} read)` : ""}`}>
          {unmatched > 0 && <div className="notice">{unmatched} marked row{unmatched === 1 ? " couldn't" : "s couldn't"} be matched to this box's list (perhaps a page from another checklist). Check the paper for anything not shown here.</div>}
          {!rows.length ? <Empty>This box is empty.</Empty> : (
            <ul className="list">
              {rows.map((r) => {
                const d = decisions[r.item_id];
                const unsure = d.confidence !== undefined && d.confidence !== null && d.confidence < 0.7;
                return (
                  <li key={r.item_id} style={{ flexWrap: "wrap", alignItems: "flex-start" }}>
                    <span className="badge">{r.row}</span>
                    <div className="grow">
                      <div className="t"><Link to={`/items/${r.item_id}`}>{r.name}</Link>{r.label ? ` (${r.label})` : ""} <span className="muted">× {r.quantity}</span></div>
                      {r.inside && <div className="s">in {r.inside}</div>}
                      {d.heard && <div className="s">Written: “{d.heard}”</div>}
                      {d.note && <div className="s">Note: {d.note}</div>}
                      {unsure && <div className="s" style={{ color: "var(--warn)" }}>The mark wasn't clear: check the paper</div>}
                    </div>
                    <div className="row wrap" style={{ gap: 6, width: "100%", justifyContent: "flex-end" }}>
                      <select aria-label={`What happened to ${r.name}`} value={d.action} onChange={(e) => { const a = e.target.value as Action; set(r.item_id, { action: a }); if (a === "move" && !d.place) setPicking(r.item_id); }}>
                        {(Object.keys(LABEL) as Action[]).map((a) => <option key={a} value={a}>{LABEL[a]}</option>)}
                      </select>
                      {d.action === "move" && <button type="button" className="btn small" onClick={() => setPicking(r.item_id)}>{d.place ? d.place.label : "Choose where"}</button>}
                      {d.action !== "dispose" && d.action !== "missing" && (
                        <input type="number" inputMode="numeric" min={0} placeholder="Qty now" aria-label={`How many ${r.name} now`} style={{ width: 96 }}
                          value={d.quantity} onChange={(e) => set(r.item_id, { quantity: e.target.value })} />
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {extras.length > 0 && (
            <div className="notice" style={{ marginTop: 8 }}>
              <b>Also found in this box</b> (written on the checklist): {extras.join("; ")}. Add them with <Link to={`/add?container_id=${cid}&return=/containers/${cid}`}>Add item</Link>, or move their records here from <Link to={`/verify?container=${cid}`}>Check contents</Link>.
            </div>
          )}
          <button className="btn primary block" style={{ marginTop: 12 }} disabled={busy || !todo.length || unresolvedMove} onClick={apply}>
            <Check size={18} /> {unresolvedMove ? "Choose where the moved things went" : `Apply ${todo.length} change${todo.length === 1 ? "" : "s"}`}
          </button>
          <p className="muted" style={{ fontSize: ".85rem" }}>Disposed things go to <b>Recently deleted</b> (you can restore them). Missing things are marked missing. It's saved as a check of this box.</p>
        </Section>
      )}
      {picking !== null && <PlacementPicker title="Where did it go?" onClose={() => setPicking(null)}
        onPick={(v) => { if (v.container_id || v.location_id) set(picking, { place: v, action: "move" }); setPicking(null); }} />}
    </div>
  );
}
