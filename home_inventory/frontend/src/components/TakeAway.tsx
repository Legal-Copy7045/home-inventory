// Take it / put away, and "what is this?" from a photo.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Camera, Hand, Home, MapPin, Minus } from "lucide-react";
import { api, type Item } from "../api";
import { Sheet, Spinner, Thumb, useToast } from "./ui";

export type TakenItem = Item & { mine: boolean };
export interface Place { container_id: number | null; location_id: number | null; label: string }
type PutResult = { result: "back" | "home" | "moved" | "temporary" | "choose"; item: Item; target: { path: string } };

const DONE: Record<Exclude<PutResult["result"], "choose">, string> = {
  back: "Back in its place",
  home: "Back home",
  moved: "Moved here",
  temporary: "Here for now; its usual place is remembered",
};

/** What you've taken and not put away yet (yours first). */
export function useTaken(enabled = true) {
  const [items, setItems] = useState<TakenItem[]>([]);
  const reload = () => {
    if (!enabled) return;
    api.get<{ items: TakenItem[] }>("taken").then((r) => setItems([...r.items].sort((a, b) => Number(b.mine) - Number(a.mine)))).catch(() => setItems([]));
  };
  useEffect(reload, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  return { items, reload };
}

export async function takeItem(id: number) {
  return api.post<Item>(`items/${id}/take`);
}

/** Put one item away at ``place``; asks "move it here, or just this once?" when the place is new to it. */
export function PutAwayButtons({ item, place, onDone }: { item: Item; place: Place; onDone: (msg: string) => void }) {
  const toast = useToast();
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async (mode: "auto" | "move" | "temporary") => {
    setBusy(true);
    try {
      const r = await api.post<PutResult>(`items/${item.id}/put-away`, { container_id: place.container_id, location_id: place.location_id, mode });
      if (r.result === "choose") setAsk(true);
      else onDone(`${item.display_name}: ${DONE[r.result]}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  if (!ask) return <button className="btn small primary" disabled={busy} onClick={() => go("auto")}>Put away here</button>;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <span className="s">New place for it. Usually: {item.home_placement?.path || item.placement.path || "nowhere yet"}</span>
      <div className="row wrap" style={{ gap: 6 }}>
        <button className="btn small primary" disabled={busy} onClick={() => go("move")}><MapPin size={14} /> Move it here</button>
        <button className="btn small" disabled={busy} onClick={() => go("temporary")}>Just this once</button>
      </div>
    </div>
  );
}

/** Shown when a label is scanned while you have things taken. */
export function PutAwaySheet({ place, items, onClose, onChanged, openLabel }: {
  place: Place; items: TakenItem[]; onClose: () => void; onChanged: () => void; openLabel?: { text: string; onClick: () => void };
}) {
  const toast = useToast();
  const [done, setDone] = useState<number[]>([]);
  const left = items.filter((i) => !done.includes(i.id));
  return (
    <Sheet title={`Put away in ${place.label}?`} onClose={onClose}>
      {left.length ? (
        <div className="list card tight">
          {left.map((it) => (
            <li key={it.id}>
              <Thumb src={it.photo?.thumb_url} />
              <div className="grow">
                <div className="t">{it.display_name}</div>
                <div className="s">{it.mine ? "You took it" : it.status_note} · lives in {it.home_placement?.path || it.placement.path || "—"}</div>
              </div>
              <PutAwayButtons item={it} place={place} onDone={(m) => { toast(m); setDone((d) => [...d, it.id]); onChanged(); }} />
            </li>
          ))}
        </div>
      ) : <p>All put away.</p>}
      <div className="stack" style={{ marginTop: 12 }}>
        {openLabel && <button className="btn block" onClick={openLabel.onClick}>{openLabel.text}</button>}
        <button className="btn ghost block" onClick={onClose}>Done</button>
      </div>
    </Sheet>
  );
}

/** A strip of what you're carrying, for the Scan page. */
export function TakenStrip({ items }: { items: TakenItem[] }) {
  const mine = items.filter((i) => i.mine);
  if (!mine.length) return null;
  return (
    <div className="notice" style={{ marginBottom: 10 }}>
      <Hand size={16} style={{ verticalAlign: "-3px" }} /> With you: {mine.map((i, n) => <span key={i.id}>{n ? ", " : ""}<Link to={`/items/${i.id}`}>{i.display_name}</Link></span>)}. Scan a label to put {mine.length === 1 ? "it" : "them"} away.
    </div>
  );
}

type FindResult = { suggestion: { name: string; manufacturer?: string | null; model_number?: string | null; confidence_label?: string }; matches: Item[] };

/**
 * "What is this?": photograph something, the AI says what it is, and you
 * pick which of your items it is, then take it, take one out, or put it here.
 */
export function PhotoFind({ here, onChanged }: { here?: Place | null; onChanged?: () => void }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<FindResult | null>(null);
  const [chosen, setChosen] = useState<Item | null>(null);

  async function onFile(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", f);
      setRes(await api.upload<FindResult>("ai/identify-to-find", form));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  const close = () => { setRes(null); setChosen(null); };
  const finish = (msg: string) => { toast(msg); close(); onChanged?.(); };
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); finish(msg); } catch (e) { toast((e as Error).message, true); }
  };

  return (
    <>
      <input ref={input} type="file" accept="image/*" hidden onChange={(e) => void onFile(e.target.files?.[0])} />
      <button className="btn block" disabled={busy} onClick={() => input.current?.click()} style={{ marginTop: 10 }}>
        <Camera size={18} /> {busy ? "Working out what it is…" : "What is this? Identify by photo"}
      </button>
      {busy && <Spinner />}
      {res && !chosen && (
        <Sheet title={`Looks like: ${res.suggestion.name}`} onClose={close}>
          {res.suggestion.confidence_label && <p className="muted" style={{ marginTop: 0 }}>{res.suggestion.confidence_label}{[res.suggestion.manufacturer, res.suggestion.model_number].some(Boolean) ? ` · ${[res.suggestion.manufacturer, res.suggestion.model_number].filter(Boolean).join(" ")}` : ""}</p>}
          {res.matches.length ? (
            <>
              <p style={{ marginTop: 0 }}>Which one is it?</p>
              <div className="list card tight">
                {res.matches.map((m) => (
                  <a key={m.id} href="#" onClick={(e) => { e.preventDefault(); setChosen(m); }}>
                    <Thumb src={m.photo?.thumb_url} />
                    <div className="grow"><div className="t">{m.display_name}</div><div className="s">{m.placement.path}{m.quantity !== 1 ? ` · ${m.quantity}` : ""}</div></div>
                  </a>
                ))}
              </div>
            </>
          ) : <p>Nothing like that in your inventory.</p>}
          <div className="stack" style={{ marginTop: 12 }}>
            <Link className="btn block" to={`/add?name=${encodeURIComponent(res.suggestion.name)}`}>None of these: add it</Link>
            <button className="btn ghost block" onClick={close}>Cancel</button>
          </div>
        </Sheet>
      )}
      {chosen && (
        <Sheet title={chosen.display_name} onClose={close}>
          <p className="muted" style={{ marginTop: 0 }}>{chosen.placement.path}{chosen.quantity !== 1 ? ` · ${chosen.quantity} here` : ""}</p>
          <div className="stack">
            {here && <PutAwayButtons item={chosen} place={here} onDone={finish} />}
            <button className="btn primary big block" onClick={() => act(() => takeItem(chosen.id), `Took ${chosen.display_name}; scan a label to put it away`)}><Hand size={20} /> Take it</button>
            {chosen.quantity > 0 && <button className="btn block" onClick={() => act(() => api.post(`items/${chosen.id}/adjust`, { delta: -1, note: "Identified by photo" }), `Took 1 × ${chosen.display_name}`)}><Minus size={18} /> Take one out (use it up)</button>}
            <Link className="btn block" to={`/items/${chosen.id}`}><Home size={18} /> Open the item</Link>
            <button className="btn ghost block" onClick={() => setChosen(null)}>Back</button>
          </div>
        </Sheet>
      )}
    </>
  );
}

type PlaceGuess = { type: "location" | "container"; id: number; path: string | null; score: number; matched: string[]; recorded: string[]; not_seen: string[] };

/** "Which shelf is this?": photograph a shelf or box; see which place it is and what should be there. */
export function WhichPlace() {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ seen: string[]; places: PlaceGuess[] } | null>(null);
  async function onFile(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", f);
      setRes(await api.upload<{ seen: string[]; places: PlaceGuess[] }>("ai/which-place", form));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  const best = res?.places[0];
  return (
    <>
      <input ref={input} type="file" accept="image/*" hidden onChange={(e) => void onFile(e.target.files?.[0])} />
      <button className="btn block" disabled={busy} onClick={() => input.current?.click()} style={{ marginTop: 8 }}>
        <MapPin size={18} /> {busy ? "Working out where this is…" : "Which shelf is this? Identify a place by photo"}
      </button>
      {res && (
        <Sheet title={best ? `Looks like ${best.path || "this place"}` : "I don't recognise this place"} onClose={() => setRes(null)}>
          {best ? (
            <div className="stack">
              <p className="muted" style={{ marginTop: 0 }}>I saw {res.seen.length} thing{res.seen.length === 1 ? "" : "s"}; {best.matched.length} of them {best.matched.length === 1 ? "is" : "are"} recorded here.</p>
              <Link className="btn primary block" to={best.type === "container" ? `/containers/${best.id}` : `/locations/${best.id}`}>Open {best.path}</Link>
              {best.not_seen.length > 0 && (
                <div className="card tight" style={{ padding: 12 }}>
                  <b>Recorded here but not in the photo</b>
                  <div className="s">{best.not_seen.join(", ")}</div>
                </div>
              )}
              {res.places.slice(1).map((p) => (
                <Link key={`${p.type}${p.id}`} className="btn block" to={p.type === "container" ? `/containers/${p.id}` : `/locations/${p.id}`}>Or: {p.path} ({p.matched.length} match{p.matched.length === 1 ? "" : "es"})</Link>
              ))}
            </div>
          ) : (
            <p>None of the things in the photo{res.seen.length ? ` (${res.seen.slice(0, 6).join(", ")})` : ""} are recorded yet. Do a photo inventory of the place to add them.</p>
          )}
        </Sheet>
      )}
    </>
  );
}
