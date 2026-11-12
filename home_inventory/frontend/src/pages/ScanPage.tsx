import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRightLeft, Box, ExternalLink, Minus, Plus, X } from "lucide-react";
import { api, labelRef, type BarcodeResult, type Container, type Item, type QrTarget } from "../api";
import { ScanWithPhone, nfcSupport } from "./Tags";
import { PlacementPicker } from "../components/PlacementPicker";
import { Scanner, type Detection } from "../components/Scanner";
import { Empty, Sheet, Thumb, useAction, useMe, useToast } from "../components/ui";

const QR_RE = /HINV[:/-][CL][:/-]([A-Z2-9]{6,16})|[?&#][cl]=([A-Z2-9]{6,16})|\/[cl]\/([A-HJ-NP-Z2-9]{6,16})/i;

export function isContainerCode(text: string) {
  return QR_RE.test(text) || /^[A-HJ-NP-Z2-9]{8}$/.test(text.trim().toUpperCase());
}

/**
 * Scan mode — built for standing in the shed with a phone:
 * scan a container label → it becomes the current container;
 * scan a product barcode → add/remove it here, or see where else you have it.
 */
export default function ScanPage() {
  const me = useMe();
  const toast = useToast();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [container, setContainer] = useState<Container | null>(null);
  const [barcode, setBarcode] = useState<{ result: BarcodeResult; format: string } | null>(null);
  const [moving, setMoving] = useState<Item | null>(null);
  const { busy, run } = useAction();
  const containerId = params.get("container");

  const loadContainer = useCallback(async (id: number | string) => {
    try {
      setContainer(await api.get<Container>(`containers/${id}`));
    } catch (e) {
      toast((e as Error).message, true);
    }
  }, [toast]);

  useEffect(() => {
    if (containerId) loadContainer(containerId);
    else setContainer(null);
  }, [containerId, loadContainer]);

  async function onDetect(d: Detection) {
    if (d.format === "qr_code" || isContainerCode(d.text)) {
      const tagRef = labelRef(d.text);
      if (tagRef?.kind === "N") {
        nav(`/tag/${tagRef.code}`); // NFC tag link (e.g. its QR fallback)
        return;
      }
      try {
        const r = await api.post<QrTarget>("qr/resolve", { text: d.text });
        if (r.type === "location") {
          nav(`/locations/${r.id}`);
          return;
        }
        setParams({ container: String(r.id) }, { replace: true });
        toast("Container opened");
      } catch (e) {
        toast((e as Error).message, true);
      }
      return;
    }
    try {
      const result = await api.post<BarcodeResult>("barcode/lookup", { code: d.text, format: d.format });
      setBarcode({ result, format: d.format });
    } catch (e) {
      toast((e as Error).message, true);
    }
  }

  const here = (r: BarcodeResult) => {
    if (!container) return null;
    for (const m of r.inventory_matches) for (const h of m.holdings) if (h.container?.id === container.id) return h;
    return null;
  };

  async function addHere(r: BarcodeResult) {
    if (!container) return;
    const h = here(r);
    if (h) {
      await run(() => api.post(`items/${h.item_id}/adjust`, { delta: 1, note: "Scan mode" }), `+1 in ${container.name}`);
    } else if (r.inventory_matches.length) {
      await run(() => api.post("items", { product_id: r.inventory_matches[0].product.id, quantity: 1, container_id: container.id }), `Added to ${container.name}`);
    } else {
      nav(`/add?barcode=${encodeURIComponent(r.barcode.raw)}&format=${barcode?.format || ""}&container_id=${container.id}&return=${encodeURIComponent(`/scan?container=${container.id}`)}`);
      return;
    }
    setBarcode(null);
    loadContainer(container.id);
  }

  async function removeHere(r: BarcodeResult) {
    const h = here(r);
    if (!h || !container) return;
    await run(() => api.post(`items/${h.item_id}/adjust`, { delta: -1, note: "Scan mode" }), `−1 from ${container.name}`);
    setBarcode(null);
    loadContainer(container.id);
  }

  async function adjust(it: Item, delta: number) {
    await run(() => api.post(`items/${it.id}/adjust`, { delta, note: "Scan mode" }));
    if (container) loadContainer(container.id);
  }

  return (
    <div className="page">
      {nfcSupport() !== "no" && !container && <ScanWithPhone />}
      {container ? (
        <div className="scanbar">
          <Box size={20} />
          <div className="t">{container.number ? `#${container.number} ` : ""}{container.name}<div style={{ fontWeight: 500, fontSize: ".8rem", opacity: 0.9 }}>{container.placement?.path || "Unassigned"}</div></div>
          <Link className="btn small" to={`/containers/${container.id}`} aria-label="Open container"><ExternalLink size={16} /></Link>
          <button className="btn small" onClick={() => setParams({}, { replace: true })} aria-label="Leave container"><X size={16} /></button>
        </div>
      ) : (
        <p className="muted">Scan a container's QR label to open it, or a product barcode to find it.</p>
      )}

      <Scanner onDetect={onDetect} paused={!!barcode || !!moving} hint={container ? "Scan a product barcode or another label" : "Scan a QR label or barcode"} />

      {container && (
        <div className="card tight" style={{ marginTop: 14 }}>
          <div className="card-title" style={{ padding: "14px 16px 0" }}>
            <h2>In this container</h2>
            {me.can_edit && <Link className="btn small" to={`/add?container_id=${container.id}&return=${encodeURIComponent(`/scan?container=${container.id}`)}`}><Plus size={16} /> Add</Link>}
          </div>
          {container.items?.length ? (
            <div className="list">
              {container.items.map((it) => (
                <li key={it.id}>
                  <Thumb src={it.photo?.thumb_url} />
                  <Link className="grow" to={`/items/${it.id}`} style={{ color: "inherit" }}>
                    <div className="t">{it.display_name}</div>
                    <div className="s">×{it.quantity}</div>
                  </Link>
                  {me.can_edit && (
                    <>
                      <button className="iconbtn" disabled={busy || it.quantity === 0} onClick={() => adjust(it, -1)} aria-label="Remove one"><Minus size={18} /></button>
                      <button className="iconbtn" disabled={busy} onClick={() => adjust(it, 1)} aria-label="Add one"><Plus size={18} /></button>
                      <button className="iconbtn" onClick={() => setMoving(it)} aria-label="Move"><ArrowRightLeft size={18} /></button>
                    </>
                  )}
                </li>
              ))}
            </div>
          ) : <Empty>Nothing recorded in here yet.</Empty>}
        </div>
      )}

      {barcode && (
        <Sheet title={barcode.result.inventory_matches[0]?.product.name || barcode.result.catalog?.name || `Barcode ${barcode.result.barcode.display}`} onClose={() => setBarcode(null)}>
          <BarcodeSummary r={barcode.result} />
          <div className="stack" style={{ marginTop: 12 }}>
            {me.can_edit && container && (
              <>
                <button className="btn primary big block" disabled={busy} onClick={() => addHere(barcode.result)}><Plus size={20} /> Add one to {container.name}</button>
                {here(barcode.result) && <button className="btn block" disabled={busy} onClick={() => removeHere(barcode.result)}><Minus size={18} /> Take one out of {container.name}</button>}
              </>
            )}
            {me.can_edit && !container && (
              <Link className="btn primary big block" to={`/add?barcode=${encodeURIComponent(barcode.result.barcode.raw)}&format=${barcode.format}`}><Plus size={20} /> {barcode.result.owned_quantity ? "Add another" : "Add to inventory"}</Link>
            )}
            {me.can_edit && barcode.result.inventory_matches[0] && (
              <button className="btn block" disabled={busy} onClick={() => run(() => api.post("shopping-list", { product_id: barcode.result.inventory_matches[0].product.id }), "Added to the shopping list")}>
                Add to shopping list
              </button>
            )}
            {barcode.result.inventory_matches[0]?.holdings[0] && <Link className="btn block" to={`/items/${barcode.result.inventory_matches[0].holdings[0].item_id}`}>View item</Link>}
            <button className="btn ghost block" onClick={() => setBarcode(null)}>Keep scanning</button>
          </div>
        </Sheet>
      )}
      {moving && (
        <PlacementPicker title={`Move ${moving.name} to…`} onClose={() => setMoving(null)} onPick={async (p) => {
          const it = moving;
          setMoving(null);
          await run(() => api.post(`items/${it.id}/move`, { container_id: p.container_id, location_id: p.location_id }), `Moved to ${p.label}`);
          if (container) loadContainer(container.id);
        }} />
      )}
    </div>
  );
}

export function BarcodeSummary({ r }: { r: BarcodeResult }) {
  return (
    <div className="stack">
      {r.owned_quantity > 0 ? (
        <div className="answer" style={{ marginBottom: 0 }}>
          I found this product in your inventory. You own {r.owned_quantity}.
          <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
            {r.inventory_matches.flatMap((m) => m.holdings).map((h) => (
              <li key={h.item_id}><Link to={`/items/${h.item_id}`}>{h.quantity} in {h.path}</Link>{h.status !== "stored" ? ` (${h.status.replace(/_/g, " ")})` : ""}</li>
            ))}
          </ul>
        </div>
      ) : r.catalog ? (
        <div className="row" style={{ alignItems: "flex-start" }}>
          {r.catalog.image_url && <img src={r.catalog.image_url} alt="" style={{ width: 72, height: 72, objectFit: "contain", borderRadius: 8, background: "#fff" }} referrerPolicy="no-referrer" />}
          <div>
            <div style={{ fontWeight: 700 }}>{r.catalog.name}</div>
            <div className="muted">{[r.catalog.manufacturer, r.catalog.model_number, r.catalog.category].filter(Boolean).join(" · ")}</div>
            {r.catalog.description && <p style={{ margin: "4px 0 0" }}>{r.catalog.description}</p>}
            <small>Not in your inventory yet · from {r.catalog.source}</small>
          </div>
        </div>
      ) : (
        <p>Not in your inventory, and no product details were found online. You can add it and type the details.</p>
      )}
      <small className="mono">{r.barcode.display} · {r.barcode.symbology.replace("_", "-").toUpperCase()}{!r.barcode.gtin ? " (not a standard product code)" : ""}</small>
    </div>
  );
}
