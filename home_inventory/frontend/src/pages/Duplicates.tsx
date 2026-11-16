import { useState } from "react";
import { Link } from "react-router-dom";
import { Merge, X } from "lucide-react";
import { api, type Attachment } from "../api";
import { Lightbox } from "../components/PhotoViewer";
import { ErrorBox, Sheet, Spinner, Thumb, useAction, useApi, useMe, useToast } from "../components/ui";

interface P { id: number; name: string; manufacturer: string | null; model_number: string | null; category: string | null; barcode: string | null; quantity: number; needs_details: boolean; photos: Attachment[] }
interface Pair { key: string; score: number; reason: string; products: [P, P] }

/** Possible duplicate products: keep one (everything moves onto it) or say they're different. */
export default function DuplicatesPage() {
  const me = useMe();
  const list = useApi(() => api.get<Pair[]>("products/duplicates"), []);
  const { busy, run } = useAction();
  const toast = useToast();
  const [ask, setAsk] = useState<{ keep: P; other: P } | null>(null);
  const [view, setView] = useState<{ photos: Attachment[]; index: number } | null>(null);
  const merge = async (keep: P, other: P, quantities: "separate" | "sum" | "same") => {
    const res = await run(() => api.post<{ combined: { quantity: number }[]; kept_apart: { why: string }[] }>(
      `products/${keep.id}/merge`, { other_id: other.id, quantities }), `Merged into ${keep.name}`);
    if (res) {
      setAsk(null);
      list.reload();
      if (res.kept_apart?.length) toast(`Some records stayed separate: ${res.kept_apart.map((k) => k.why).join("; ")}`);
    }
  };
  const dismiss = async (pair: Pair) => {
    if (await run(() => api.post("products/duplicates/dismiss", { product_ids: pair.products.map((p) => p.id) }), "Won't suggest that pair again")) list.reload();
  };
  const desc = (p: P) => [p.manufacturer, p.model_number, p.category, p.barcode && `barcode ${p.barcode}`].filter(Boolean).join(" · ");
  return (
    <div className="page">
      <h1>Possible duplicates</h1>
      <p className="muted">Things recorded twice under slightly different names, the same barcode, or the same maker and model. To merge a pair, tap <b>Merge into this one</b> on the one to keep: the other's holdings, photos, shop links and restock settings move onto it, its missing details are filled in, and you choose whether quantities kept in the same place are added together.</p>
      <ErrorBox error={list.error} />
      {list.error && !list.data ? <ErrorBox error={list.error} retry={list.reload} /> : list.loading && !list.data ? <Spinner /> : list.data?.length ? list.data.map((pair) => (
        <div key={pair.key} className="card">
          <div className="muted" style={{ fontSize: ".85rem", marginBottom: 8 }}>{pair.reason} · {Math.round(pair.score * 100)}% match</div>
          <div className="grid two">
            {pair.products.map((p, i) => (
              <div key={p.id} className="dup-side">
                <div className="row wrap" style={{ gap: 6, marginBottom: 6 }}>
                  {p.photos.length ? p.photos.map((ph, n) => (
                    <button key={ph.id} type="button" className="thumb-btn" aria-label={`Show photo ${n + 1} of ${p.name}`}
                      onClick={() => setView({ photos: p.photos, index: n })}>
                      <Thumb src={ph.thumb_url || ph.url} size={n === 0 ? "md" : ""} />
                    </button>
                  )) : <Thumb />}
                </div>
                <div className="t" style={{ fontWeight: 700 }}><Link to={`/items?q=${encodeURIComponent(p.name)}`}>{p.name}</Link>{p.needs_details && <span className="badge blue" style={{ marginLeft: 6 }}>Needs details</span>}</div>
                <div className="muted" style={{ fontSize: ".9rem" }}>{desc(p) || "No other details"}</div>
                <div className="muted" style={{ fontSize: ".9rem" }}>You have {p.quantity}</div>
                {me.can_edit && (
                  <button className="btn small primary" style={{ marginTop: 8 }} disabled={busy} onClick={() => setAsk({ keep: p, other: pair.products[1 - i] })}>
                    <Merge size={16} /> Merge into this one
                  </button>
                )}
              </div>
            ))}
          </div>
          {me.can_edit && <button className="btn small ghost" style={{ marginTop: 8 }} disabled={busy} onClick={() => dismiss(pair)}><X size={16} /> Not the same thing</button>}
        </div>
      )) : <div className="card"><div className="empty">No likely duplicates found.</div></div>}
      {view && <Lightbox photos={view.photos} index={view.index} onIndex={(i) => setView({ ...view, index: i })} onClose={() => setView(null)} />}
      {ask && (
        <Sheet title={`Merge into “${ask.keep.name}”`} onClose={() => setAsk(null)}>
          <p>“{ask.other.name}” (you have {ask.other.quantity}) becomes “{ask.keep.name}” (you have {ask.keep.quantity}). Where both are kept in the same place, what should happen to the quantities?</p>
          <div className="stack">
            <button className="btn primary choice" disabled={busy} onClick={() => merge(ask.keep, ask.other, "sum")}>
              Add them together <small>two boxes: 2 and 3 become one record of 5</small>
            </button>
            <button className="btn choice" disabled={busy} onClick={() => merge(ask.keep, ask.other, "same")}>
              It's the same thing recorded twice <small>one record, the larger count</small>
            </button>
            <button className="btn choice" disabled={busy} onClick={() => merge(ask.keep, ask.other, "separate")}>
              Keep the records separate <small>quantities unchanged</small>
            </button>
          </div>
          <p className="muted" style={{ fontSize: ".85rem" }}>Things in different places always stay separate. A record that's lent out, in use, managed equipment, tagged, in a kit or a customer's is never combined.</p>
        </Sheet>
      )}
    </div>
  );
}
