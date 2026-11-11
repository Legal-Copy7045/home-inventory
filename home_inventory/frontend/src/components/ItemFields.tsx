import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api, type LinkDetails } from "../api";
import { useApi, useMe } from "./ui";

export interface ItemFormValue {
  name: string;
  description: string;
  category: string;
  manufacturer: string;
  model_number: string;
  barcode: string;
  barcode_format: string;
  sku: string;
  tags: string;
  url: string;
  label: string;
  notes: string;
  quantity: number;
  min_quantity: string;
  expires_on: string;
  purchase_price: string;
  currency: string;
  purchase_date: string;
  retailer: string;
  warranty_start: string;
  warranty_expires: string;
  serial_number: string;
}

export const emptyItem = (currency: string): ItemFormValue => ({
  name: "",
  description: "",
  category: "",
  manufacturer: "",
  model_number: "",
  barcode: "",
  barcode_format: "",
  sku: "",
  tags: "",
  url: "",
  label: "",
  notes: "",
  quantity: 1,
  min_quantity: "",
  expires_on: "",
  purchase_price: "",
  currency,
  purchase_date: "",
  retailer: "",
  warranty_start: "",
  warranty_expires: "",
  serial_number: "",
});

/** Convert form strings to the API payload; empty strings become null. */
export function toPayload(v: ItemFormValue, opts: { includeQuantity?: boolean } = {}) {
  const n = (s: string) => (s.trim() === "" ? null : s.trim());
  const out: Record<string, unknown> = {
    name: v.name.trim(),
    description: n(v.description),
    category: n(v.category),
    manufacturer: n(v.manufacturer),
    model_number: n(v.model_number),
    barcode: n(v.barcode),
    sku: n(v.sku),
    tags: n(v.tags),
    url: n(v.url),
    label: n(v.label),
    notes: n(v.notes),
    min_quantity: v.min_quantity.trim() === "" ? null : parseInt(v.min_quantity, 10),
    expires_on: n(v.expires_on),
    purchase_price: n(v.purchase_price),
    currency: v.purchase_price.trim() ? n(v.currency) : null,
    purchase_date: n(v.purchase_date),
    retailer: n(v.retailer),
    warranty_start: n(v.warranty_start),
    warranty_expires: n(v.warranty_expires),
    serial_number: n(v.serial_number),
  };
  if (v.barcode_format) out.barcode_format = v.barcode_format;
  if (opts.includeQuantity !== false) out.quantity = v.quantity;
  return out;
}

export function ItemFields({
  value,
  onChange,
  showQuantity = true,
  sharedCount = 0,
}: {
  value: ItemFormValue;
  onChange: (v: ItemFormValue) => void;
  showQuantity?: boolean;
  sharedCount?: number;
}) {
  const me = useMe();
  const cats = useApi(() => api.get<{ id: number; name: string }[]>("categories"), []);
  const [more, setMore] = useState(!!(value.purchase_price || value.warranty_expires || value.serial_number || value.retailer));
  const set = (k: keyof ItemFormValue) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    onChange({ ...value, [k]: e.target.value });

  return (
    <div className="stack">
      {sharedCount > 0 && (
        <div className="notice">
          Name, maker, model, category and barcode are shared with {sharedCount} other record{sharedCount === 1 ? "" : "s"} of this product.
        </div>
      )}
      <label className="field">
        Name
        <input required value={value.name} onChange={set("name")} placeholder="e.g. Cordless drill" autoComplete="off" />
      </label>
      <div className="grid two">
        <label className="field">
          Category
          <input list="cat-list" value={value.category} onChange={set("category")} placeholder="Tools, Kitchen…" />
          <datalist id="cat-list">{cats.data?.map((c) => <option key={c.id} value={c.name} />)}</datalist>
        </label>
        <label className="field">
          Manufacturer
          <input value={value.manufacturer} onChange={set("manufacturer")} />
        </label>
        <label className="field">
          Model number
          <input value={value.model_number} onChange={set("model_number")} />
        </label>
        <label className="field">
          Barcode (UPC/EAN)
          <input inputMode="numeric" value={value.barcode} onChange={set("barcode")} />
        </label>
      </div>
      <label className="field">
        Product link
        <span className="hint">Shop or manufacturer page for this product</span>
        <input type="url" inputMode="url" value={value.url} onChange={set("url")} placeholder="https://…" />
      </label>
      <label className="field">
        Description
        <textarea value={value.description} onChange={set("description")} rows={2} />
      </label>
      {showQuantity && (
        <label className="field">
          Quantity
          <input type="number" inputMode="numeric" min={0} value={value.quantity} onChange={(e) => onChange({ ...value, quantity: Math.max(0, parseInt(e.target.value || "0", 10) || 0) })} />
        </label>
      )}
      <div className="grid two">
        <label className="field">
          Expiry / use-by date
          <span className="hint">Food, medicine, batteries, chemicals…</span>
          <input type="date" value={value.expires_on} onChange={set("expires_on")} />
        </label>
        <label className="field">
          Low-stock alert below
          <span className="hint">Total across all places (optional)</span>
          <input type="number" inputMode="numeric" min={0} value={value.min_quantity} onChange={set("min_quantity")} placeholder="e.g. 4" />
        </label>
      </div>
      <button type="button" className="btn ghost" onClick={() => setMore(!more)} style={{ justifyContent: "flex-start", paddingLeft: 0 }}>
        {more ? <ChevronUp size={18} /> : <ChevronDown size={18} />} Purchase, warranty &amp; more
      </button>
      {more && (
        <div className="stack">
          <div className="grid two">
            <label className="field">
              Purchase price
              <div className="row">
                <input type="number" inputMode="decimal" step="0.01" min={0} value={value.purchase_price} onChange={set("purchase_price")} />
                <input style={{ width: 84 }} maxLength={3} value={value.currency || me.app.currency} onChange={(e) => onChange({ ...value, currency: e.target.value.toUpperCase() })} />
              </div>
            </label>
            <label className="field">
              Purchase date
              <input type="date" value={value.purchase_date} onChange={set("purchase_date")} />
            </label>
            <label className="field">
              Retailer
              <input value={value.retailer} onChange={set("retailer")} />
            </label>
            <label className="field">
              Serial number
              <input value={value.serial_number} onChange={set("serial_number")} />
            </label>
            <label className="field">
              Warranty start
              <input type="date" value={value.warranty_start} onChange={set("warranty_start")} />
            </label>
            <label className="field">
              Warranty expires
              <input type="date" value={value.warranty_expires} onChange={set("warranty_expires")} />
            </label>
            <label className="field">
              SKU
              <input value={value.sku} onChange={set("sku")} />
            </label>
            <label className="field">
              Label for this one
              <span className="hint">e.g. "the red one"</span>
              <input value={value.label} onChange={set("label")} />
            </label>
          </div>
          <label className="field">
            Search keywords
            <span className="hint">Extra words to find it by, comma separated</span>
            <input value={value.tags} onChange={set("tags")} />
          </label>
          <label className="field">
            Notes
            <textarea value={value.notes} onChange={set("notes")} rows={2} />
          </label>
        </div>
      )}
    </div>
  );
}

/** Merge details read from a product web page into the form.  With
 * ``overwrite`` false only empty fields are filled, so nothing typed by the
 * user is lost. */
export function applyLinkDetails(v: ItemFormValue, d: LinkDetails, overwrite: boolean): ItemFormValue {
  const pick = (cur: string, next: string | null | undefined) => (next && (overwrite || !cur.trim()) ? next : cur);
  return {
    ...v,
    url: d.url || v.url,
    name: pick(v.name, d.name),
    description: pick(v.description, d.description),
    category: pick(v.category, d.category),
    manufacturer: pick(v.manufacturer, d.manufacturer),
    model_number: pick(v.model_number, d.model_number),
    barcode: pick(v.barcode, d.barcode),
    sku: pick(v.sku, d.sku),
    purchase_price: pick(v.purchase_price, d.price),
    currency: d.price && d.currency && (overwrite || !v.purchase_price.trim()) ? d.currency : v.currency,
    retailer: pick(v.retailer, d.retailer),
  };
}
