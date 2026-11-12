// Thin API client.  All URLs are relative to the document so the app works
// under Home Assistant Ingress (/api/hassio_ingress/<token>/) and behind the
// integration's sidebar proxy alike.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function url(path: string): string {
  const p = path.replace(/^\//, "");
  return new URL(p, document.baseURI).toString();
}

async function handle<T>(r: Response): Promise<T> {
  if (r.status === 204) return undefined as T;
  const type = r.headers.get("content-type") || "";
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    if (type.includes("json")) {
      const body = await r.json().catch(() => null);
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    }
    throw new ApiError(r.status, msg);
  }
  if (type.includes("json")) return (await r.json()) as T;
  return (await r.blob()) as unknown as T;
}

type Query = Record<string, string | number | boolean | undefined | null>;

function qs(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  get: <T>(path: string, q?: Query) => fetch(url(`api/v1/${path}${qs(q)}`), { credentials: "same-origin" }).then(handle<T>),
  send: <T>(method: string, path: string, body?: unknown, q?: Query) =>
    fetch(url(`api/v1/${path}${qs(q)}`), {
      method,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }).then(handle<T>),
  post: <T>(path: string, body?: unknown) => api.send<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => api.send<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => api.send<T>("PUT", path, body),
  del: <T = void>(path: string, q?: Query) => api.send<T>("DELETE", path, undefined, q),
  upload: <T>(path: string, form: FormData) =>
    fetch(url(`api/v1/${path}`), { method: "POST", body: form, credentials: "same-origin" }).then(handle<T>),
  blob: (method: string, path: string, body?: unknown) =>
    fetch(url(`api/v1/${path}`), {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (r) => {
      if (!r.ok) await handle(r);
      return r.blob();
    }),
};

export async function uploadPhoto(file: Blob, opts: { kind?: string; item_id?: number; container_id?: number; location_id?: number; name?: string } = {}) {
  const f = new FormData();
  f.append("file", file, opts.name || (file instanceof File ? file.name : "photo.jpg"));
  f.append("kind", opts.kind || "photo");
  if (opts.item_id) f.append("item_id", String(opts.item_id));
  if (opts.container_id) f.append("container_id", String(opts.container_id));
  if (opts.location_id) f.append("location_id", String(opts.location_id));
  return api.upload<Attachment>("attachments", f);
}

// ---------------------------------------------------------------- types

export interface Attachment {
  id: number;
  kind: string;
  mime_type: string;
  original_name: string | null;
  size_bytes: number;
  width: number | null;
  height: number | null;
  caption: string | null;
  url: string;
  thumb_url: string | null;
  created_at: string;
}

export interface Ref {
  id: number;
  name: string;
}
export interface ContainerRef extends Ref {
  number: string | null;
  qr_code: string;
  label: string;
}
export interface Placement {
  container: ContainerRef | null;
  container_path: ContainerRef[];
  location: Ref | null;
  location_path: Ref[];
  effective_location_id: number | null;
  path: string;
}

export interface Loan {
  id: number;
  item_id: number;
  borrower: string;
  quantity: number;
  loaned_at: string;
  expected_return: string | null;
  returned_at: string | null;
  notes: string | null;
  overdue: boolean;
  item?: Item;
}

export interface MapLink {
  id: number;
  title: string;
  thumb_url: string;
}

export type ItemStatus = "stored" | "in_use" | "temporarily_relocated" | "on_loan" | "awaiting_return" | "missing";

export interface Product {
  id: number;
  name: string;
  description: string | null;
  category: string | null;
  manufacturer: string | null;
  model_number: string | null;
  barcode: string | null;
  gtin: string | null;
  sku: string | null;
  tags: string | null;
  url: string | null;
  min_quantity: number | null;
  identification_status: string;
  catalog_metadata: Record<string, unknown> | null;
  ai_metadata: Record<string, unknown> | null;
}

export interface Item {
  id: number;
  product_id: number;
  name: string;
  label: string | null;
  display_name: string;
  category: string | null;
  manufacturer: string | null;
  model_number: string | null;
  barcode: string | null;
  product_url: string | null;
  quantity: number;
  status: ItemStatus;
  status_note: string | null;
  placement: Placement;
  home_placement: Placement | null;
  photo: Attachment | null;
  loan: Loan | null;
  warranty_expires: string | null;
  warranty_active: boolean;
  expires_on: string | null;
  expired: boolean;
  days_to_expiry: number | null;
  min_quantity: number | null;
  low_stock: boolean;
  purchase_price: string | null;
  currency: string | null;
  identification_status: string;
  needs_details: boolean;
  capture: { ai_status: "queued" | "ready" | "failed" | "none"; suggestion: AISuggestion | null } | null;
  deleted_at: string | null;
  maps?: MapLink[];
  // detail
  product?: Product;
  notes?: string | null;
  serial_number?: string | null;
  purchase_date?: string | null;
  retailer?: string | null;
  warranty_start?: string | null;
  attachments?: Attachment[];
  loans?: Loan[];
  siblings?: { id: number; quantity: number; label: string | null; status: string; path: string }[];
  created_at?: string;
}

export interface Container {
  id: number;
  name: string;
  number: string | null;
  label: string;
  description: string | null;
  container_type: string | null;
  qr_code: string;
  location_id: number | null;
  parent_container_id: number | null;
  placement: Placement | null;
  path: string;
  temporarily_moved: boolean;
  home_placement: Placement | null;
  item_count: number;
  container_count: number;
  photo: Attachment | null;
  deleted_at: string | null;
  items?: Item[];
  containers?: Container[];
  photos?: Attachment[];
  maps?: MapLink[];
}

export interface LocationNode {
  id: number;
  name: string;
  kind: string | null;
  parent_id: number | null;
  item_count: number;
  container_count: number;
  children: LocationNode[];
}

export interface LocationDetail {
  id: number;
  name: string;
  kind: string | null;
  description: string | null;
  parent_id: number | null;
  path: string;
  qr_code: string | null;
  path_parts: Ref[];
  children?: { id: number; name: string; kind: string | null }[];
  items?: Item[];
  containers?: Container[];
  maps?: MapLink[];
  deleted_at: string | null;
}

export interface FlatLocation {
  id: number;
  name: string;
  kind: string | null;
  parent_id: number | null;
  qr_code: string | null;
  path: string;
}

export interface HistoryEvent {
  id: number;
  ts: string;
  user: string | null;
  action: string;
  entity_type: string;
  entity_id: number;
  summary: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface Stats {
  total_items: number;
  item_records: number;
  products: number;
  containers: number;
  locations: number;
  total_value: number;
  currency: string;
  on_loan: number;
  loans_active: number;
  loans_overdue: number;
  temporarily_relocated: number;
  warranties_expiring: number;
  expiring_soon: number;
  expired: number;
  low_stock: number;
  missing: number;
  needs_details: number;
}

export interface LowStock {
  product_id: number;
  name: string;
  quantity: number;
  min_quantity: number | null;
  item_id: number | null;
}

export interface Dashboard {
  stats: Stats;
  recent_activity: HistoryEvent[];
  loans: (Loan & { item: Item })[];
  warranties: Item[];
  expiring: Item[];
  low_stock: LowStock[];
}

export interface Me {
  id: number;
  name: string;
  role: "admin" | "member" | "read_only";
  can_edit: boolean;
  is_admin: boolean;
  /** True only while nobody can administer the app (first-run setup code). */
  admin_setup?: boolean;
  channel: string;
  app: {
    version: string;
    ai: { enabled: boolean; provider?: string; model?: string; error?: string };
    currency: string;
    qr_base_url: string;
    label_templates: { key: string; name: string }[];
  };
}

export interface AISuggestion {
  name: string;
  description: string | null;
  category: string | null;
  manufacturer: string | null;
  model_number: string | null;
  confidence: number | null;
  confidence_label: string;
  quantity?: number;
}

export interface SimilarProduct {
  product: Product;
  similarity: number;
  holdings: { id: number; quantity: number; label: string | null; status: string; path: string }[];
}

export interface BarcodeResult {
  barcode: { raw: string; display: string; gtin: string | null; symbology: string; valid: boolean };
  inventory_matches: {
    product: Product;
    holdings: { item_id: number; quantity: number; status: string; label: string | null; path: string; container: ContainerRef | null }[];
  }[];
  owned_quantity: number;
  catalog: { name?: string; description?: string; manufacturer?: string; model_number?: string; category?: string; image_url?: string; source?: string; url?: string } | null;
  message: string | null;
}

export interface LinkDetails {
  url: string;
  name: string | null;
  description: string | null;
  manufacturer: string | null;
  model_number: string | null;
  barcode: string | null;
  sku: string | null;
  category: string | null;
  image_url: string | null;
  price: string | null;
  currency: string | null;
  retailer: string | null;
  source: string;
}

export interface LinkImport {
  details: LinkDetails;
  photo: Attachment | null;
  image_error: string | null;
  similar: SimilarProduct[];
  existing_product: Product | null;
}

export interface SearchResult {
  query: string;
  interpretation: { intent: string; terms: string[][]; manufacturer: string | null; by: string };
  answer: string;
  total_matches: number;
  total_quantity: number;
  results: Item[];
  ai_error?: string;
}

export interface Scan {
  id: number;
  container_id: number;
  status: "pending" | "analysing" | "ready" | "failed" | "confirmed" | "discarded";
  provider: string | null;
  model: string | null;
  error: string | null;
  photo: Attachment | null;
  suggestions: (AISuggestion & {
    quantity: number;
    match: { item_id: number; name: string; quantity: number; similarity: number } | null;
    proposed_action: "add" | "increase" | "skip";
  })[];
  created_at: string;
}

export interface MapMarker {
  id?: number;
  x: number;
  y: number;
  label: string | null;
  target: { type: "location" | "container"; id: number; name: string; number?: string | null; deleted?: boolean };
}

export interface LocationMap {
  id: number;
  title: string;
  location_id: number | null;
  location_path: string | null;
  photo: Attachment;
  marker_count: number;
  markers?: MapMarker[];
}

export const STATUS_LABELS: Record<ItemStatus, string> = {
  stored: "In storage",
  in_use: "In use",
  temporarily_relocated: "Temporarily moved",
  on_loan: "Lent out",
  awaiting_return: "Awaiting return",
  missing: "Missing",
};

// ---------------------------------------------------------------- QR label links

const CODE_RE = "([A-HJ-NP-Z2-9]{6,16})";

/** Address of this App as the user opened it in Home Assistant, e.g.
 * https://ha.example.com/b1a256fc_home_inventory.  Used in QR label links so a
 * phone camera opens the right page.  Null when it can't be determined. */
export function appBaseUrl(): string | null {
  try {
    const top = window.top!.location;
    const first = top.pathname.split("/").filter(Boolean)[0];
    if (!first || first === "api" || first === "local") return null;
    return `${top.origin}/${first}`;
  } catch {
    return null; // not same-origin (shouldn't happen under Ingress)
  }
}

/** Find a label reference (/c/CODE, /l/CODE, ?c=CODE) in a URL path/query. */
export function labelRef(s: string): { kind: "C" | "L"; code: string } | null {
  const m = new RegExp(`/([cl])/${CODE_RE}(?=$|[/?#])`, "i").exec(s) || new RegExp(`[?&]([cl])=${CODE_RE}`, "i").exec(s);
  return m ? { kind: m[1].toUpperCase() as "C" | "L", code: m[2].toUpperCase() } : null;
}

export function qrImageUrl(kind: "containers" | "locations", id: number): string {
  const base = appBaseUrl();
  return url(`api/v1/${kind}/${id}/qr.svg${base ? `?base=${encodeURIComponent(base)}` : ""}`);
}

export interface QrTarget {
  type: "container" | "location";
  id: number;
}
