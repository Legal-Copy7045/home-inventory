import { Link } from "react-router-dom";
import { Check, X } from "lucide-react";
import { api } from "../api";
import { ErrorBox, Spinner, useAction, useApi, useMe } from "../components/ui";

interface P { id: number; name: string; manufacturer: string | null; model_number: string | null; category: string | null; barcode: string | null; quantity: number; needs_details: boolean }
interface Pair { key: string; score: number; reason: string; products: [P, P] }

/** Possible duplicate products: keep one (everything moves onto it) or say they're different. */
export default function DuplicatesPage() {
  const me = useMe();
  const list = useApi(() => api.get<Pair[]>("products/duplicates"), []);
  const { busy, run } = useAction();
  const merge = async (keep: P, other: P) => {
    if (await run(() => api.post(`products/${keep.id}/merge`, { other_id: other.id }), `Merged into ${keep.name}`)) list.reload();
  };
  const dismiss = async (pair: Pair) => {
    if (await run(() => api.post("products/duplicates/dismiss", { product_ids: pair.products.map((p) => p.id) }), "Won't suggest that pair again")) list.reload();
  };
  const desc = (p: P) => [p.manufacturer, p.model_number, p.category, p.barcode && `barcode ${p.barcode}`].filter(Boolean).join(" · ");
  return (
    <div className="page">
      <h1>Possible duplicates</h1>
      <p className="muted">Things recorded twice under slightly different names, the same barcode, or the same maker and model. Choose which one to keep: the other's holdings, photos, shop links and restock settings move onto it, and its missing details are filled in. Holdings stay in their own places.</p>
      <ErrorBox error={list.error} />
      {list.error && !list.data ? <ErrorBox error={list.error} retry={list.reload} /> : list.loading && !list.data ? <Spinner /> : list.data?.length ? list.data.map((pair) => (
        <div key={pair.key} className="card">
          <div className="muted" style={{ fontSize: ".85rem", marginBottom: 8 }}>{pair.reason} · {Math.round(pair.score * 100)}% match</div>
          <div className="grid two">
            {pair.products.map((p, i) => (
              <div key={p.id} className="dup-side">
                <div className="t" style={{ fontWeight: 700 }}><Link to={`/items?q=${encodeURIComponent(p.name)}`}>{p.name}</Link>{p.needs_details && <span className="badge blue" style={{ marginLeft: 6 }}>Needs details</span>}</div>
                <div className="muted" style={{ fontSize: ".9rem" }}>{desc(p) || "No other details"}</div>
                <div className="muted" style={{ fontSize: ".9rem" }}>You have {p.quantity}</div>
                {me.can_edit && (
                  <button className="btn small primary" style={{ marginTop: 8 }} disabled={busy} onClick={() => merge(p, pair.products[1 - i])}>
                    <Check size={16} /> Keep this one
                  </button>
                )}
              </div>
            ))}
          </div>
          {me.can_edit && <button className="btn small ghost" style={{ marginTop: 8 }} disabled={busy} onClick={() => dismiss(pair)}><X size={16} /> Not the same thing</button>}
        </div>
      )) : <div className="card"><div className="empty">No likely duplicates found.</div></div>}
    </div>
  );
}
