import { useState } from "react";
import { FileSpreadsheet, FileText, MapPin, Printer, X } from "lucide-react";
import { api, url } from "../api";
import { PlacementPicker } from "../components/PlacementPicker";
import { ErrorBox, Spinner, Thumb, fmtDate, money, useApi } from "../components/ui";

interface Row {
  item_id: number;
  place: string;
  room: string;
  name: string;
  category: string | null;
  manufacturer: string | null;
  model_number: string | null;
  serial_number: string | null;
  quantity: number;
  purchase_date: string | null;
  retailer: string | null;
  unit_price: string | null;
  currency: string | null;
  value: string | null;
  warranty_expires: string | null;
  photos: number;
  receipts: number;
  documents: number;
  thumb_url: string | null;
}
interface Report {
  currency: string;
  count: number;
  total_value: string;
  unvalued: number;
  groups: { room: string; count: number; value: string; items: Row[] }[];
}

/**
 * Inventory / insurance report: everything you own grouped by room, with
 * photos, serial numbers, recorded values and paperwork - printable, or as
 * CSV / Excel.  Values are what you paid, not replacement cost.
 */
export default function ReportPage() {
  const [place, setPlace] = useState<{ location_id: number | null; container_id: number | null; label: string } | null>(null);
  const [category, setCategory] = useState("");
  const [picking, setPicking] = useState(false);
  const cats = useApi(() => api.get<{ id: number; name: string }[]>("categories"), []);
  const filters: Record<string, string | number | undefined> = {
    location_id: place?.location_id ?? undefined,
    container_id: place?.container_id ?? undefined,
    category: category || undefined,
  };
  const rep = useApi(() => api.get<Report>("reports/inventory", filters), [place?.label, category]);
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString();
  const file = (fmt: string) => url(`api/v1/reports/inventory.${fmt}${qs ? `?${qs}` : ""}`);
  const r = rep.data;

  return (
    <div className="page report">
      <div className="row between wrap no-print">
        <h1 style={{ margin: 0 }}>Inventory report</h1>
        <div className="row wrap" style={{ gap: 6 }}>
          <button className="btn small" onClick={() => window.print()}><Printer size={16} /> Print / PDF</button>
          <a className="btn small" href={file("csv")} download><FileText size={16} /> CSV</a>
          <a className="btn small" href={file("xlsx")} download><FileSpreadsheet size={16} /> Excel</a>
        </div>
      </div>
      <p className="muted no-print">For insurance or a record of what you own. Values are the prices you recorded, times quantity. Customer equipment is never included.</p>
      <div className="row wrap no-print" style={{ margin: "10px 0" }}>
        <button className="btn small" onClick={() => setPicking(true)}><MapPin size={16} /> {place ? place.label : "Everywhere"}</button>
        {place && <button className="btn small ghost" onClick={() => setPlace(null)} aria-label="Everywhere"><X size={16} /></button>}
        <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ width: "auto" }} aria-label="Category">
          <option value="">All categories</option>
          {cats.data?.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
      </div>
      <ErrorBox error={rep.error} />
      {rep.loading && !r ? <Spinner /> : r && (
        <>
          <div className="report-head">
            <h2 className="print-only">Home inventory{place ? ` — ${place.label}` : ""}{category ? ` — ${category}` : ""}</h2>
            <div className="grid stats">
              <div className="stat"><div className="v">{r.count}</div><div className="l">Items</div></div>
              <div className="stat"><div className="v" style={{ fontSize: "1.2rem" }}>{money(r.total_value, r.currency)}</div><div className="l">Recorded value</div></div>
              {r.unvalued > 0 && <div className="stat"><div className="v">{r.unvalued}</div><div className="l">Without a price</div></div>}
            </div>
            <p className="muted print-only">Generated {new Date().toLocaleString()}</p>
          </div>
          {r.groups.map((g) => (
            <section key={g.room} className="card tight report-room">
              <div className="card-title" style={{ padding: "14px 16px 0" }}>
                <h2>{g.room}</h2>
                <span className="muted">{g.count} item{g.count === 1 ? "" : "s"} · {money(g.value, r.currency)}</span>
              </div>
              <ul className="list">
                {g.items.map((it) => (
                  <li key={it.item_id}>
                    <Thumb src={it.thumb_url} />
                    <div className="grow">
                      <div className="t">{it.name}{it.quantity !== 1 ? ` × ${it.quantity}` : ""}</div>
                      <div className="s">{[it.manufacturer, it.model_number, it.serial_number && `S/N ${it.serial_number}`].filter(Boolean).join(" · ") || it.category || ""}</div>
                      <div className="s">{it.place}{it.purchase_date ? ` · bought ${fmtDate(it.purchase_date)}` : ""}{it.retailer ? ` at ${it.retailer}` : ""}{it.warranty_expires ? ` · warranty to ${fmtDate(it.warranty_expires)}` : ""}</div>
                      <div className="s">{it.receipts ? `${it.receipts} receipt${it.receipts === 1 ? "" : "s"}` : "No receipt"}{it.documents ? ` · ${it.documents} document${it.documents === 1 ? "" : "s"}` : ""}</div>
                    </div>
                    <div style={{ textAlign: "right", fontWeight: 600 }}>{it.value ? money(it.value, it.currency || r.currency) : <span className="muted">—</span>}</div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {!r.count && <div className="card"><div className="empty">Nothing matches.</div></div>}
        </>
      )}
      {picking && (
        <PlacementPicker title="Report for…" onClose={() => setPicking(false)} onPick={(p) => {
          setPicking(false);
          setPlace(p.location_id === null && p.container_id === null ? null : p);
        }} />
      )}
    </div>
  );
}
