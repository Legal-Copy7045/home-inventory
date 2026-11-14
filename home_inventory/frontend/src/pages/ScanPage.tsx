import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRightLeft, Box, ExternalLink, Hand, Minus, Plus, X } from "lucide-react";
import { api, labelRef, type BarcodeResult, type Container, type Item, type QrTarget } from "../api";
import { ScanWithPhone, nfcSupport } from "./Tags";
import { PlacementPicker } from "../components/PlacementPicker";
import { Scanner, type Detection } from "../components/Scanner";
import { PhotoFind, PutAwaySheet, WhichPlace, TakenStrip, takeItem, useTaken, type Place, type TakenItem } from "../components/TakeAway";
import { Empty, Sheet, Thumb, useAction, useMe, useToast } from "../components/ui";

type UseChoice = { item_id: number; name: string; quantity: number; path: string };
type UseOneResult = { used: { item_id: number; name: string; remaining: number; path: string; low_stock: boolean } | null; choices: UseChoice[] };

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
  // Things you've taken: scanning a label offers to put them away there.
  const taken = useTaken(me.can_edit);
  const [putAway, setPutAway] = useState<{ place: Place; items: TakenItem[]; open: () => void; openText: string } | null>(null);
  // "Take one out": scanning a consumable's barcode uses one straight away.
  const useMode = params.get("mode") === "use";
  const [choices, setChoices] = useState<{ code: string; format: string; list: UseChoice[] } | null>(null);
  const setMode = (use: boolean) => {
    const p = new URLSearchParams(params);
    if (use) p.set("mode", "use");
    else p.delete("mode");
    setParams(p, { replace: true });
  };

  async function takeOne(code: string, format: string, itemId?: number) {
    try {
      const r = await api.post<UseOneResult>("barcode/use-one", { code, format, item_id: itemId ?? null, container_id: container?.id ?? null });
      if (r.used) {
        setChoices(null);
        if (navigator.vibrate) navigator.vibrate(40);
        toast(`Took 1 × ${r.used.name} — ${r.used.remaining} left${r.used.low_stock ? " (running low)" : ""}`);
        if (container) loadContainer(container.id);
      } else setChoices({ code, format, list: r.choices });
    } catch (e) {
      toast((e as Error).message, true);
    }
  }

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
    const job = labelRef(d.text);
    if (job?.kind === "R") {
      try {
        const j = await api.get<{ id: number }>(`repair-jobs/by-number/${encodeURIComponent(job.code)}`);
        nav(`/repairs/${j.id}`);
      } catch (e) {
        toast((e as Error).message, true);
      }
      return;
    }
    if (d.format === "qr_code" || isContainerCode(d.text)) {
      const tagRef = labelRef(d.text);
      if (tagRef?.kind === "N") {
        nav(`/tag/${tagRef.code}`); // NFC tag link (e.g. its QR fallback)
        return;
      }
      try {
        const r = await api.post<QrTarget>("qr/resolve", { text: d.text });
        const open = () => {
          setPutAway(null);
          if (r.type === "location") nav(`/locations/${r.id}`);
          else { setParams({ container: String(r.id) }, { replace: true }); toast("Container opened"); }
        };
        // Ask the server, not the list loaded when the page opened: a label
        // scanned straight after "Take it" must still offer to put it away.
        const carrying = me.can_edit ? (await api.get<{ items: TakenItem[] }>("taken")).items.filter((i) => i.mine) : [];
        if (carrying.length) {
          taken.reload();
          const label = r.type === "location" ? (await api.get<{ path: string }>(`locations/${r.id}`)).path
                                              : (await api.get<{ path: string; name: string }>(`containers/${r.id}`)).name;
          setPutAway({ place: { container_id: r.type === "location" ? null : r.id, location_id: r.type === "location" ? r.id : null, label },
                       items: carrying, open, openText: r.type === "location" ? "Open this place" : "Open this box" });
          return;
        }
        open();
      } catch (e) {
        toast((e as Error).message, true);
      }
      return;
    }
    if (useMode && me.can_edit) {
      await takeOne(d.text, d.format);
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

      {me.can_edit && (
        <div className="tabs" role="tablist" aria-label="When a barcode is scanned">
          <button role="tab" aria-selected={!useMode} className={!useMode ? "active" : ""} onClick={() => setMode(false)}>Look up / add</button>
          <button role="tab" aria-selected={useMode} className={useMode ? "active" : ""} onClick={() => setMode(true)}><Minus size={14} /> Take one out</button>
        </div>
      )}
      {useMode && <p className="muted" style={{ marginTop: 0 }}>Each barcode scanned takes one out of stock (from this container when one is open). Restock alerts update straight away.</p>}

      <TakenStrip items={taken.items} />
      <Scanner onDetect={onDetect} paused={!!barcode || !!moving || !!choices || !!putAway} hint={container ? "Scan a product barcode or another label" : "Scan a QR label or barcode"} />

      {me.can_edit && me.app.ai.enabled && (
        <PhotoFind here={container ? { container_id: container.id, location_id: null, label: container.name } : null}
                   onChanged={() => { taken.reload(); if (container) loadContainer(container.id); }} />
      )}
      {me.can_edit && me.app.ai.enabled && !container && <WhichPlace />}

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
            {me.can_edit && barcode.result.inventory_matches.flatMap((m) => m.holdings).length === 1 && (() => {
              const h = barcode.result.inventory_matches[0].holdings[0];
              return <button className="btn block" disabled={busy} onClick={async () => { await run(() => takeItem(h.item_id), "Taken; scan a label to put it away"); setBarcode(null); taken.reload(); }}><Hand size={18} /> Take it</button>;
            })()}
            {barcode.result.inventory_matches[0]?.holdings[0] && <Link className="btn block" to={`/items/${barcode.result.inventory_matches[0].holdings[0].item_id}`}>View item</Link>}
            <button className="btn ghost block" onClick={() => setBarcode(null)}>Keep scanning</button>
          </div>
        </Sheet>
      )}
      {choices && (
        <Sheet title="Take one from which?" onClose={() => setChoices(null)}>
          <div className="list card tight">
            {choices.list.map((c) => (
              <a key={c.item_id} href="#" onClick={(e) => { e.preventDefault(); void takeOne(choices.code, choices.format, c.item_id); }}>
                <div className="grow"><div className="t">{c.name}</div><div className="s">{c.path}</div></div>
                <b>{c.quantity}</b>
              </a>
            ))}
          </div>
        </Sheet>
      )}
      {putAway && (
        <PutAwaySheet place={putAway.place} items={putAway.items} onClose={() => setPutAway(null)} onChanged={() => { taken.reload(); if (container) loadContainer(container.id); }}
                      openLabel={{ text: putAway.openText, onClick: putAway.open }} />
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
