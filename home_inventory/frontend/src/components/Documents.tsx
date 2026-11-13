import { useEffect, useState } from "react";
import { BookOpen, FileText, Sparkles, Trash2 } from "lucide-react";
import { ManualSheet } from "./Parts";
import { api, url, type Attachment } from "../api";
import { Sheet, fmtDate, useAction, useApi, useMe } from "./ui";

interface FieldSuggestion { field: string; label: string; value: string; current: string | null; note: string | null; selected: boolean }

interface DocReading {
  id: number;
  attachment_id: number;
  status: "pending" | "ready" | "failed" | "applied" | "dismissed";
  error: string | null;
  result: { document_type: string; title: string; summary: string | null; matches_item: boolean; confidence: number | null } | null;
  suggestions?: { fields: FieldSuggestion[]; kind: string | null; caption: string | null; is_manual: boolean };
}

const TYPE: Record<string, string> = {
  receipt: "Receipt", invoice: "Invoice", order_confirmation: "Order confirmation", warranty: "Warranty",
  manual: "Manual", spec_sheet: "Specification sheet", insurance: "Insurance document", certificate: "Certificate",
  service_record: "Service record", other: "Document",
};
const KIND: Record<string, string> = { receipt: "Receipt", warranty: "Warranty", manual: "Manual", document: "Document" };

const isDate = (f: string) => ["purchase_date", "warranty_start", "warranty_expires"].includes(f);
const show = (f: string, v: string | null) => (v && isDate(f) ? fmtDate(v) : v);

/** "Receipts & documents" on an item: each document with what AI found in it. */
export function DocumentsList({ itemId, docs, edit, onChanged, onRemove }: {
  itemId: number; docs: Attachment[]; edit: boolean; onChanged: () => void; onRemove: (d: Attachment) => void;
}) {
  const me = useMe();
  const readings = useApi(() => api.get<DocReading[]>(`items/${itemId}/document-readings`), [itemId, docs.length]);
  const [review, setReview] = useState<{ r: DocReading; doc: Attachment } | null>(null);
  const [manual, setManual] = useState(false);
  const { busy, run } = useAction();
  const pending = readings.data?.some((r) => r.status === "pending");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => readings.reload(), 3000);
    return () => clearInterval(t);
  }, [pending]); // eslint-disable-line react-hooks/exhaustive-deps
  const byDoc = Object.fromEntries((readings.data || []).map((r) => [r.attachment_id, r]));
  const recognise = async (d: Attachment) => {
    if (await run(() => api.post(`attachments/${d.id}/recognise`))) readings.reload();
  };

  return (
    <>
      <div className="list">
        {docs.map((d) => {
          const r = byDoc[d.id];
          const s = r?.suggestions;
          const todo = s ? s.fields.length + (s.kind ? 1 : 0) : 0;
          return (
            <li key={d.id}>
              <FileText size={20} />
              <div className="grow">
                <a href={url(d.url)} target="_blank" rel="noreferrer">
                  <div className="t">{d.caption || d.original_name || KIND[d.kind] || d.kind}</div>
                  <div className="s">{KIND[d.kind] || d.kind}{d.caption && d.original_name ? ` · ${d.original_name}` : ""} · {fmtDate(d.created_at)}</div>
                </a>
                {r?.status === "pending" && <div className="s"><Sparkles size={14} /> AI is reading this…</div>}
                {r?.status === "ready" && r.result && (
                  <div className="s">
                    <Sparkles size={14} /> {TYPE[r.result.document_type] || "Document"}
                    {!r.result.matches_item ? " – looks like it's for a different product" : todo ? ` – ${todo} thing${todo === 1 ? "" : "s"} to fill in` : " – nothing new to fill in"}

                  </div>
                )}
                {r?.status === "failed" && <div className="s">AI couldn't read it: {r.error}</div>}
                {edit && r?.status === "ready" && (
                  <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
                    {todo > 0 && r.result?.matches_item && <button className="btn small primary" onClick={() => setReview({ r, doc: d })}>Review</button>}
                    <button className="btn small ghost" disabled={busy} onClick={() => run(() => api.post(`document-readings/${r.id}/dismiss`)).then(() => readings.reload())}>Dismiss</button>
                  </div>
                )}
                {edit && me.app.ai.enabled && (d.kind === "manual" || r?.result?.document_type === "manual") && (
                  <button className="btn small" style={{ marginTop: 4 }} onClick={() => setManual(true)}>
                    <BookOpen size={14} /> Read for the service schedule and parts
                  </button>
                )}
                {edit && me.app.ai.enabled && (!r || r.status === "failed") && (
                  <button className="btn small ghost" style={{ marginTop: 4, padding: "2px 6px" }} disabled={busy} onClick={() => recognise(d)}>
                    <Sparkles size={14} /> {r ? "Try again" : "Read with AI"}
                  </button>
                )}
              </div>
              {edit && <button className="iconbtn" onClick={() => onRemove(d)} aria-label="Remove"><Trash2 size={18} /></button>}
            </li>
          );
        })}
      </div>
      {manual && <ManualSheet itemId={itemId} onClose={() => setManual(false)} onDone={() => { setManual(false); onChanged(); }} />}
      {review && (
        <ReviewSheet r={review.r} doc={review.doc} onClose={() => setReview(null)}
          onDone={() => { setReview(null); readings.reload(); onChanged(); }} />
      )}
    </>
  );
}

function ReviewSheet({ r, doc, onClose, onDone }: { r: DocReading; doc: Attachment; onClose: () => void; onDone: () => void }) {
  const s = r.suggestions!;
  const [rows, setRows] = useState(s.fields.map((f) => ({ ...f })));
  const [setKind, setSetKind] = useState(!!s.kind);
  const [caption, setCaption] = useState(s.caption || "");
  const { busy, run } = useAction();
  const apply = async () => {
    const values = Object.fromEntries(rows.filter((x) => x.selected).map((x) => [x.field, x.value]));
    const body = { values, kind: setKind ? s.kind : null, caption: s.caption && caption ? caption : null };
    if (await run(() => api.post(`document-readings/${r.id}/apply`, body), "Saved to the item")) onDone();
  };
  return (
    <Sheet title={r.result?.title || "Document"} onClose={onClose}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          {r.result?.summary} Check these against the document. Empty fields are ticked; anything that would replace what's already there is left for you to decide.
        </p>
        {s.kind && (
          <label className="row" style={{ gap: 8 }}>
            <input type="checkbox" checked={setKind} onChange={(e) => setSetKind(e.target.checked)} />
            <span>File it as a <b>{KIND[s.kind]}</b> <span className="muted">(now: {KIND[doc.kind] || doc.kind})</span></span>
          </label>
        )}
        {s.caption && <label className="field">Name<input value={caption} onChange={(e) => setCaption(e.target.value)} /></label>}
        {rows.map((f, i) => (
          <div key={f.field} className="notice stack" style={{ gap: 4 }}>
            <label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={f.selected} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, selected: e.target.checked } : x)))} />
              <b>{f.label}</b>
            </label>
            <input type={isDate(f.field) ? "date" : "text"} value={f.value} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value, selected: true } : x)))} />
            {f.current && <span className="hint">Replaces {show(f.field, f.current)}</span>}
            {f.note && <span className="hint">{f.note}</span>}
          </div>
        ))}
        <button className="btn primary block" disabled={busy || (!rows.some((x) => x.selected) && !setKind && !(s.caption && caption))} onClick={apply}>Save to the item</button>
      </div>
    </Sheet>
  );
}
