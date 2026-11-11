import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Box, ChevronRight, MapPin, Package, X } from "lucide-react";
import { url, type Container, type Item, type Me, type Placement, STATUS_LABELS } from "../api";

// ---------------------------------------------------------------- data hooks

export function useApi<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const load = useCallback(() => {
    const n = ++seq.current;
    setLoading(true);
    fn()
      .then((d) => {
        if (n === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => n === seq.current && setError(e.message || String(e)))
      .finally(() => n === seq.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(load, [load]);
  return { data, error, loading, reload: load, setData };
}

// ---------------------------------------------------------------- app context

export const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext)!;

type ToastFn = (msg: string, error?: boolean) => void;
export const ToastContext = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastHost({ children }: { children: ReactNode }) {
  const [t, setT] = useState<{ msg: string; err: boolean } | null>(null);
  const timer = useRef<number>(0);
  const show = useCallback<ToastFn>((msg, err = false) => {
    setT({ msg, err });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setT(null), err ? 5000 : 2600);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {t && (
        <div className={`toast ${t.err ? "err" : ""}`} role="status" onClick={() => setT(null)}>
          {t.msg}
        </div>
      )}
    </ToastContext.Provider>
  );
}

/** Wrap an async action with busy state and error toast. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
      setBusy(true);
      try {
        const r = await fn();
        if (ok) toast(ok);
        return r;
      } catch (e) {
        toast((e as Error).message || "Something went wrong", true);
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { busy, run };
}

// ---------------------------------------------------------------- primitives

export function Spinner() {
  return (
    <div className="center">
      <div className="spinner" aria-label="Loading" />
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error">{error}</div> : null;
}

export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Thumb({ src, size = "", icon }: { src?: string | null; size?: string; icon?: ReactNode }) {
  return <div className={`thumb ${size}`}>{src ? <img src={url(src)} alt="" loading="lazy" /> : icon || <Package size={24} />}</div>;
}

export function StatusBadge({ item }: { item: Pick<Item, "status" | "loan" | "expired" | "days_to_expiry" | "low_stock"> }) {
  const out: ReactNode[] = [];
  if (item.status !== "stored") {
    const cls = item.status === "missing" ? "danger" : item.status === "on_loan" ? (item.loan?.overdue ? "danger" : "warn") : "blue";
    const label = item.status === "on_loan" && item.loan ? `Lent to ${item.loan.borrower}${item.loan.overdue ? " · overdue" : ""}` : STATUS_LABELS[item.status];
    out.push(
      <span key="s" className={`badge ${cls}`}>
        {label}
      </span>,
    );
  }
  if (item.expired) out.push(<span key="e" className="badge danger">Expired</span>);
  else if (item.days_to_expiry !== null && item.days_to_expiry !== undefined && item.days_to_expiry <= 14)
    out.push(
      <span key="e" className="badge warn">
        Expires {item.days_to_expiry === 0 ? "today" : `in ${item.days_to_expiry}d`}
      </span>,
    );
  if (item.low_stock) out.push(<span key="l" className="badge warn">Low stock</span>);
  return <>{out}</>;
}

export function PathView({ placement, link = true }: { placement: Placement | null; link?: boolean }) {
  if (!placement || (!placement.location_path.length && !placement.container_path.length)) return <span className="muted">Unassigned</span>;
  const parts = [
    ...placement.location_path.map((l) => ({ key: `l${l.id}`, to: `/locations/${l.id}`, name: l.name })),
    ...placement.container_path.map((c) => ({ key: `c${c.id}`, to: `/containers/${c.id}`, name: c.label })),
  ];
  return (
    <span className="path">
      {parts.map((p, i) => (
        <span key={p.key} className="row" style={{ gap: 4 }}>
          {i > 0 && <ChevronRight size={12} />}
          {link ? <Link to={p.to}>{p.name}</Link> : p.name}
        </span>
      ))}
    </span>
  );
}

export function ItemRow({ item, right }: { item: Item; right?: ReactNode }) {
  const nav = useNavigate();
  return (
    <Link to={`/items/${item.id}`}>
      <Thumb src={item.photo?.thumb_url} />
      <div className="grow">
        <div className="t">{item.display_name}</div>
        <div className="s">{item.placement.path}</div>
        <div className="row wrap" style={{ gap: 4, marginTop: 2 }}>
          <StatusBadge item={item} />
          {/* Map links are buttons, not links: a link inside the row link is invalid HTML. */}
          {item.maps?.map((m) => (
            <button
              key={m.id}
              type="button"
              className="badge blue maplink"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                nav(`/maps/${m.id}`);
              }}
            >
              <MapPin size={12} /> {m.title}
            </button>
          ))}
        </div>
      </div>
      {right ?? <div className="qty">×{item.quantity}</div>}
    </Link>
  );
}

export function ContainerRow({ c }: { c: Container }) {
  return (
    <Link to={`/containers/${c.id}`}>
      <Thumb src={c.photo?.thumb_url} icon={<Box size={24} />} />
      <div className="grow">
        <div className="t">
          {c.number ? <span className="badge blue" style={{ marginRight: 6 }}>#{c.number}</span> : null}
          {c.name}
        </div>
        <div className="s">{c.placement?.path || "Unassigned"}</div>
      </div>
      <div className="s" style={{ textAlign: "right" }}>
        {c.item_count} item{c.item_count === 1 ? "" : "s"}
        {c.container_count ? <div>{c.container_count} inside</div> : null}
      </div>
    </Link>
  );
}

export function Stepper({ value, onChange, min = 0 }: { value: number; onChange: (n: number) => void; min?: number }) {
  return (
    <span className="stepper">
      <button type="button" onClick={() => onChange(Math.max(min, value - 1))} aria-label="Decrease">
        −
      </button>
      <input
        type="number"
        inputMode="numeric"
        value={value}
        min={min}
        onChange={(e) => onChange(Math.max(min, parseInt(e.target.value || "0", 10) || 0))}
      />
      <button type="button" onClick={() => onChange(value + 1)} aria-label="Increase">
        +
      </button>
    </span>
  );
}

export function fmtDate(s?: string | null) {
  if (!s) return "";
  const d = new Date(s.length === 10 ? `${s}T12:00:00` : s);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtWhen(s: string) {
  const d = new Date(s);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return fmtDate(s);
}

export function money(v: number | string | null | undefined, currency: string) {
  if (v === null || v === undefined || v === "") return "";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(v));
  } catch {
    return `${v} ${currency}`;
  }
}

export function Section({ title, action, children, tight }: { title: string; action?: ReactNode; children: ReactNode; tight?: boolean }) {
  return (
    <div className={`card ${tight ? "tight" : ""}`}>
      <div className="card-title" style={tight ? { padding: "14px 16px 0" } : undefined}>
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
