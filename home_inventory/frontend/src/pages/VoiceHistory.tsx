// Admin → Voice history: what was asked by voice, and what was answered.
import { useState } from "react";
import { Mic, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { ErrorBox, Spinner, fmtWhen, useAction, useApi } from "../components/ui";

interface Entry {
  at: string; source: string; device: string | null; user: string | null; intent: string | null;
  slots: Record<string, string | null>; text: string | null; answer: string | null; question: boolean;
}

const QUESTION: Record<string, string> = {
  HomeInventoryWhereIs: "Where is",
  HomeInventoryWhereDoesItGo: "Where does it go",
  HomeInventoryMove: "Moved",
  HomeInventoryChoose: "Reply",
  HomeInventoryMaintenanceDue: "Maintenance due",
  HomeInventoryRunningLow: "Running low",
  HomeInventoryOnLoan: "Lent out",
};
const SOURCE: Record<string, string> = { alexa: "Alexa", conversation: "Assist", homeassistant: "Assist" };
interface Phrase { phrase: string; count: number; last: string; suggestions: { product_id: number; name: string; score: number }[] }
const unanswered = (e: Entry) => /^(I couldn't find|Sorry|There's nothing)/.test(e.answer || "");

export default function VoiceHistoryPage() {
  const list = useApi(() => api.get<{ entries: Entry[] }>("voice/history"), []);
  const { busy, run } = useAction();
  const [onlyMissed, setOnlyMissed] = useState(false);
  const sugg = useApi(() => api.get<{ phrases: Phrase[] }>("voice/suggestions"), []);
  if (!list.data) return list.error ? <div className="page"><ErrorBox error={list.error} /></div> : <Spinner />;
  const rows = list.data.entries.filter((e) => !onlyMissed || unanswered(e));
  return (
    <div className="page">
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}>Voice history</h1>
        {list.data.entries.length > 0 && (
          <button className="btn small ghost" disabled={busy} onClick={async () => {
            if (!window.confirm("Clear the voice history?")) return;
            if (await run(() => api.del("voice/history"), "Voice history cleared")) list.reload();
          }}><Trash2 size={16} /> Clear</button>
        )}
      </div>
      <p className="muted">Questions asked through Alexa, Assist or Siri (through Assist), and the answers, newest first. The last 500 are kept for 90 days, on this machine only. "Sorry, I didn't catch that" from Alexa isn't here: that question never reached Home Assistant (check the Alexa app's voice history).</p>
      {(sugg.data?.phrases.length ?? 0) > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <h3 style={{ marginTop: 0 }}>Names it didn't recognise</h3>
          <p className="muted">Add a name people say as another name for the item, and voice and search will find it.</p>
          <div className="list">
            {sugg.data!.phrases.map((p) => (
              <li key={p.phrase} style={{ alignItems: "flex-start" }}>
                <div className="grow">
                  <div className="t">“{p.phrase}” <span className="muted">· asked {p.count}×</span></div>
                  <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
                    {p.suggestions.length ? p.suggestions.map((s) => (
                      <button key={s.product_id} className="btn small" disabled={busy} onClick={async () => {
                        if (await run(() => api.post(`products/${s.product_id}/aliases`, { alias: p.phrase }), `“${p.phrase}” now finds ${s.name}`)) sugg.reload();
                      }}><Plus size={14} /> Another name for {s.name}</button>
                    )) : <span className="s">No likely match. Is it in the inventory?</span>}
                  </div>
                </div>
              </li>
            ))}
          </div>
        </div>
      )}
      <label className="row" style={{ gap: 8, marginBottom: 10 }}>
        <input type="checkbox" checked={onlyMissed} onChange={(e) => setOnlyMissed(e.target.checked)} /> Only questions it couldn't answer
      </label>
      <div className="list card tight">
        {rows.map((e, i) => (
          <li key={`${e.at}-${i}`}>
            <span className="ico"><Mic size={18} /></span>
            <div className="grow">
              <div className="t">
                {QUESTION[e.intent || ""] || e.intent || "Question"}
                {Object.values(e.slots).filter(Boolean).length > 0 && <>: “{Object.values(e.slots).filter(Boolean).join(" → ")}”</>}
                {unanswered(e) && <span className="badge warn" style={{ marginLeft: 6 }}>not found</span>}
                {e.question && <span className="badge" style={{ marginLeft: 6 }}>asked back</span>}
              </div>
              {e.text && <div className="s">Said: {e.text}</div>}
              <div className="s">{e.answer || "—"}</div>
              <div className="s muted">{fmtWhen(e.at)} · {SOURCE[e.source] || e.source}{e.device ? ` · ${e.device}` : ""}{e.user ? ` · ${e.user}` : ""}</div>
            </div>
          </li>
        ))}
        {!rows.length && <div className="empty">{onlyMissed ? "Every question was answered." : "No voice questions yet."}</div>}
      </div>
    </div>
  );
}
