import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Camera, ImagePlus, MapPin, Sparkles, Trash2 } from "lucide-react";
import { api, uploadPhoto, type AISuggestion, type Attachment, type BarcodeResult, type Item, type SimilarProduct } from "../api";
import { ItemFields, applyLinkDetails, emptyItem, toPayload, type ItemFormValue } from "../components/ItemFields";
import { LinkImport } from "../components/LinkImport";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { useAction, useMe, useToast } from "../components/ui";

/**
 * Mobile-first add flow: photo → optional AI identification → review/edit →
 * place → save.  AI results only ever pre-fill the form.
 */
const LAST_PLACE = "hinv.lastPlace";
function lastPlace(): { container_id: number | null; location_id: number | null } | null {
  try { return JSON.parse(window.localStorage.getItem(LAST_PLACE) || "null"); } catch { return null; }
}
function rememberPlace(p: { container_id: number | null; location_id: number | null }) {
  try { window.localStorage.setItem(LAST_PLACE, JSON.stringify({ container_id: p.container_id, location_id: p.location_id })); } catch { /* private mode */ }
}

export default function AddItem() {
  const me = useMe();
  const nav = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const [photos, setPhotos] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [value, setValue] = useState<ItemFormValue>(emptyItem(me.app.currency));
  const [placement, setPlacement] = useState<PlacementValue & { remembered?: boolean }>({ container_id: null, location_id: null, label: "Unassigned" });
  const [picking, setPicking] = useState(false);
  const [ai, setAi] = useState<{ suggestion: AISuggestion; metadata: Record<string, unknown> } | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [similar, setSimilar] = useState<SimilarProduct[]>([]);
  const [productId, setProductId] = useState<number | null>(null);
  const [individual, setIndividual] = useState(false);
  const [catalog, setCatalog] = useState<BarcodeResult["catalog"]>(null);
  const { busy, run } = useAction();

  // Prefill from query string (Scan mode, container page, barcode results)
  useEffect(() => {
    const cid = params.get("container_id");
    const lid = params.get("location_id");
    if (cid) api.get<{ id: number; path: string }>(`containers/${cid}`).then((c) => setPlacement({ container_id: c.id, location_id: null, label: c.path }));
    else if (lid) api.get<{ id: number; path: string }>(`locations/${lid}`).then((l) => setPlacement({ location_id: l.id, container_id: null, label: l.path }));
    else {
      // Most things go where the last one went: start there (if it still exists).
      const last = lastPlace();
      if (last?.container_id) api.get<{ id: number; path: string }>(`containers/${last.container_id}`).then((c) => setPlacement({ container_id: c.id, location_id: null, label: c.path, remembered: true })).catch(() => undefined);
      else if (last?.location_id) api.get<{ id: number; path: string }>(`locations/${last.location_id}`).then((l) => setPlacement({ location_id: l.id, container_id: null, label: l.path, remembered: true })).catch(() => undefined);
    }
    const pid = params.get("product_id");
    if (pid) {
      setProductId(Number(pid));
      const iid = params.get("item_id");
      if (iid)
        api.get<Item>(`items/${iid}`).then((it) =>
          setValue((v) => ({ ...v, name: it.name, category: it.category || "", manufacturer: it.manufacturer || "", model_number: it.model_number || "" })),
        );
    }
    const code = params.get("barcode");
    if (code) {
      const fmt = params.get("format") || "";
      setValue((v) => ({ ...v, barcode: code, barcode_format: fmt }));
      api.post<BarcodeResult>("barcode/lookup", { code, format: fmt || null }).then((r) => {
        const p = r.inventory_matches[0]?.product;
        if (p) {
          setProductId(p.id);
          setValue((v) => ({ ...v, name: p.name, category: p.category || "", manufacturer: p.manufacturer || "", model_number: p.model_number || "", description: p.description || "" }));
        } else if (r.catalog) {
          setCatalog(r.catalog);
          const c = r.catalog;
          setValue((v) => ({ ...v, name: c.name || v.name, manufacturer: c.manufacturer || "", model_number: c.model_number || "", category: c.category || "", description: c.description || "" }));
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Suggest existing products with a similar name (avoid accidental duplicates)
  useEffect(() => {
    if (productId || value.name.trim().length < 3) {
      setSimilar([]);
      return;
    }
    const t = window.setTimeout(() => {
      api.get<SimilarProduct[]>("products/similar", { name: value.name }).then(setSimilar).catch(() => setSimilar([]));
    }, 400);
    return () => window.clearTimeout(t);
  }, [value.name, productId]);

  async function addPhotos(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      const added: Attachment[] = [];
      for (const f of Array.from(files)) added.push(await uploadPhoto(f));
      setPhotos((p) => [...p, ...added]);
      if (!photos.length && me.app.ai.enabled && !value.name) identify(added[0]);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setUploading(false);
    }
  }

  async function identify(photo = photos[0]) {
    if (!photo) return;
    setAiBusy(true);
    setAiError(null);
    try {
      const r = await api.post<{ suggestion: AISuggestion; metadata: Record<string, unknown>; similar: SimilarProduct[] }>("ai/identify-item", { attachment_id: photo.id });
      setAi(r);
      const s = r.suggestion;
      setValue((v) => ({
        ...v,
        name: s.name || v.name,
        description: s.description || v.description,
        category: s.category || v.category,
        manufacturer: s.manufacturer || v.manufacturer,
        model_number: s.model_number || v.model_number,
      }));
    } catch (e) {
      setAiError(`${(e as Error).message}. You can fill in the details yourself.`);
    } finally {
      setAiBusy(false);
    }
  }

  async function save(addAnother: boolean) {
    if (!value.name.trim() && !productId) {
      toast("Please enter a name", true);
      return;
    }
    const body: Record<string, unknown> = {
      ...toPayload(value),
      product_id: productId,
      container_id: placement.container_id,
      location_id: placement.location_id,
      photo_ids: photos.map((p) => p.id),
      track_individually: individual,
      identification_status: ai ? "confirmed" : catalog ? "catalog" : "manual",
      ai_metadata: ai ? { ...ai.metadata, summary: ai.suggestion.name } : null,
      catalog_metadata: catalog || null,
    };
    const res = await run(() => api.post<{ items: Item[] }>("items", body), "Saved");
    if (!res) return;
    if (placement.container_id || placement.location_id) rememberPlace(placement);
    if (addAnother) {
      setPhotos([]);
      setValue({ ...emptyItem(me.app.currency) });
      setAi(null);
      setProductId(null);
      setCatalog(null);
      window.scrollTo(0, 0);
    } else {
      nav(params.get("return") || `/items/${res.items[0].id}`, { replace: true });
    }
  }

  const chosen = similar.find((s) => s.product.id === productId);

  return (
    <div className="page">
      <h1>Add item</h1>

      <div className="card">
        <div className="card-title">
          <h2>Photos</h2>
          {uploading && <div className="spinner" />}
        </div>
        {photos.length > 0 && (
          <div className="photos" style={{ marginBottom: 12 }}>
            {photos.map((p, i) => (
              <div key={p.id} className="ph">
                <img src={new URL(p.thumb_url || p.url, document.baseURI).toString()} alt="" />
                {i === 0 && <span className="badge blue star">Main</span>}
                <button className="iconbtn" style={{ position: "absolute", top: 0, right: 0, color: "#fff" }} onClick={() => setPhotos(photos.filter((x) => x.id !== p.id))} aria-label="Remove photo">
                  <Trash2 size={18} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="row wrap">
          <label className="btn primary big" style={{ flex: 1 }}>
            <Camera size={20} /> Take photo
            <input type="file" accept="image/*" hidden onChange={(e) => addPhotos(e.target.files)} />
          </label>
          <label className="btn big" style={{ flex: 1 }}>
            <ImagePlus size={20} /> Upload
            <input type="file" accept="image/*" multiple hidden onChange={(e) => addPhotos(e.target.files)} />
          </label>
        </div>
        {me.app.ai.enabled && photos.length > 0 && (
          <button className="btn block" style={{ marginTop: 10 }} onClick={() => identify()} disabled={aiBusy}>
            {aiBusy ? <div className="spinner" /> : <Sparkles size={18} />} {aiBusy ? "Identifying…" : ai ? "Identify again" : "Identify with AI"}
          </button>
        )}
        {ai && (
          <p style={{ marginTop: 10, marginBottom: 0 }}>
            <Sparkles size={14} /> AI suggestion — <b className={`conf-${ai.suggestion.confidence_label}`}>{ai.suggestion.confidence_label} confidence</b>. Check and edit the details below before saving.
          </p>
        )}
        {aiError && <div className="notice" style={{ marginTop: 10, marginBottom: 0 }}>{aiError}</div>}
        {catalog && (
          <p style={{ marginTop: 10, marginBottom: 0 }} className="muted">
            Details pre-filled from {catalog.source}. Please check them.
          </p>
        )}
      </div>

      {similar.length > 0 && !productId && (
        <div className="card">
          <h2>Already own one of these?</h2>
          <p className="muted">Add another of an existing product instead of creating a duplicate.</p>
          <div className="list">
            {similar.slice(0, 4).map((s) => (
              <li key={s.product.id}>
                <div className="grow">
                  <div className="t">{s.product.name}</div>
                  <div className="s">{s.holdings.map((h) => `${h.quantity} in ${h.path}`).join(" · ")}</div>
                </div>
                <button className="btn small" onClick={() => { setProductId(s.product.id); setValue((v) => ({ ...v, name: s.product.name, category: s.product.category || "", manufacturer: s.product.manufacturer || "", model_number: s.product.model_number || "" })); }}>
                  Add another
                </button>
              </li>
            ))}
          </div>
        </div>
      )}
      {productId && (
        <div className="notice row between">
          <span>Adding another {chosen?.product.name || value.name}. Existing records are not changed.</span>
          <button className="btn small" onClick={() => setProductId(null)}>Make separate</button>
        </div>
      )}

      <div className="card">
        <h2>Details</h2>
        <ItemFields value={value} onChange={setValue} />
        {value.quantity > 1 && (
          <label className="check" style={{ marginTop: 10 }}>
            <input type="checkbox" checked={individual} onChange={(e) => setIndividual(e.target.checked)} />
            Track each one separately (e.g. tools with serial numbers)
          </label>
        )}
      </div>

      <div className="card">
        <h2>Where is it?</h2>
        <button className="btn block" style={{ justifyContent: "flex-start" }} onClick={() => setPicking(true)}>
          <MapPin size={18} /> {placement.label}
        </button>
        {placement.remembered && <span className="hint">Same place as the last thing you added. Tap to change.</span>}
      </div>

      <div className="card">
        <div className="card-title">
          <h2>Or fill it in from a web link</h2>
        </div>
        <LinkImport
          initialUrl={params.get("url") || ""}
          onImported={(r) => {
            setValue((v) => applyLinkDetails(v, r.details, !v.name.trim()));
            if (r.photo) setPhotos((p) => [...p, r.photo!]);
            const d = r.details;
            setCatalog({
              name: d.name ?? undefined, description: d.description ?? undefined, manufacturer: d.manufacturer ?? undefined,
              model_number: d.model_number ?? undefined, category: d.category ?? undefined, image_url: d.image_url ?? undefined,
              url: d.url, source: d.retailer || new URL(d.url).hostname,
            });
            if (r.existing_product) setProductId(r.existing_product.id);
          }}
        />
      </div>


      <div className="row" style={{ position: "sticky", bottom: "calc(var(--nav-h) + var(--safe-bottom) + 8px)" }}>
        <button className="btn" style={{ flex: 1 }} disabled={busy} onClick={() => save(true)}>
          Save &amp; add another
        </button>
        <button className="btn primary" style={{ flex: 1 }} disabled={busy} onClick={() => save(false)}>
          Save
        </button>
      </div>
      {picking && <PlacementPicker onClose={() => setPicking(false)} onPick={(p) => { setPlacement(p); setPicking(false); }} />}
    </div>
  );
}
