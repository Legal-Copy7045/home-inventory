import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, Download, Printer, RotateCcw, Upload } from "lucide-react";
import { api, url, type Container, type HistoryEvent, type Item, type Loan, type LowStock } from "../api";
import { ContainerRow, Empty, ErrorBox, ItemRow, Section, Spinner, fmtDate, fmtWhen, useAction, useApi, useMe, useToast } from "../components/ui";

// ---------------------------------------------------------------- labels

export function LabelsPage() {
  const me = useMe();
  const [params] = useSearchParams();
  const all = useApi(() => api.get<Container[]>("containers"), []);
  const [sel, setSel] = useState<Set<number>>(new Set((params.get("ids") || "").split(",").filter(Boolean).map(Number)));
  const [template, setTemplate] = useState(() => localStorageGet("labelTemplate") || me.app.label_templates[0]?.key || "avery_l7160");
  const [contents, setContents] = useState(false);
  const [location, setLocation] = useState(true);
  const [copies, setCopies] = useState(1);
  const [start, setStart] = useState(1);
  const { busy, run } = useAction();
  useEffect(() => localStorageSet("labelTemplate", template), [template]);
  const toggle = (id: number) => { const s = new Set(sel); if (s.has(id)) s.delete(id); else s.add(id); setSel(s); };

  async function print() {
    const blob = await run(() => api.blob("POST", "labels/pdf", { container_ids: [...sel], template, include_contents: contents, include_location: location, copies, start_position: start }));
    if (!blob) return;
    const href = URL.createObjectURL(blob);
    const w = window.open(href, "_blank");
    if (!w) {
      const a = document.createElement("a");
      a.href = href;
      a.download = "labels.pdf";
      a.click();
    }
    window.setTimeout(() => URL.revokeObjectURL(href), 60000);
  }

  return (
    <div className="page">
      <h1>Print labels</h1>
      <div className="card stack">
        <label className="field">Label sheet / printer
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>{me.app.label_templates.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}</select>
        </label>
        <div className="grid two">
          <label className="field">Copies of each<input type="number" min={1} max={20} value={copies} onChange={(e) => setCopies(Math.max(1, Number(e.target.value) || 1))} /></label>
          <label className="field">Start at label position<span className="hint">Reuse a partly used sheet</span><input type="number" min={1} value={start} onChange={(e) => setStart(Math.max(1, Number(e.target.value) || 1))} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={location} onChange={(e) => setLocation(e.target.checked)} /> Print location</label>
        <label className="check"><input type="checkbox" checked={contents} onChange={(e) => setContents(e.target.checked)} /> Print contents list</label>
      </div>
      <div className="row between" style={{ margin: "8px 0" }}>
        <b>{sel.size} selected</b>
        <div className="row"><button className="btn small" onClick={() => setSel(new Set(all.data?.map((c) => c.id)))}>All</button><button className="btn small" onClick={() => setSel(new Set())}>None</button></div>
      </div>
      <div className="card tight">
        {all.loading ? <Spinner /> : (
          <div className="list">
            {all.data?.map((c) => (
              <label key={c.id} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
                <div className="grow" style={{ flex: 1 }}><b>{c.number ? `#${c.number} ` : ""}{c.name}</b><div className="muted" style={{ fontSize: ".85rem" }}>{c.placement?.path || "Unassigned"}</div></div>
                <span className="mono muted">{c.qr_code}</span>
              </label>
            ))}
            {!all.data?.length && <Empty>Create containers first.</Empty>}
          </div>
        )}
      </div>
      <button className="btn primary big block" disabled={!sel.size || busy} onClick={print}><Printer size={20} /> Create PDF</button>
    </div>
  );
}

function localStorageGet(k: string) { try { return window.localStorage.getItem(`hinv.${k}`); } catch { return null; } }
function localStorageSet(k: string, v: string) { try { window.localStorage.setItem(`hinv.${k}`, v); } catch { /* private mode */ } }

// ---------------------------------------------------------------- activity

export function ActivityPage() {
  const [events, setEvents] = useState<HistoryEvent[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  async function load(before?: number) {
    setLoading(true);
    const r = await api.get<{ events: HistoryEvent[]; next_before_id: number | null }>("history", { before_id: before, action: filter, limit: 50 });
    setEvents((e) => (before ? [...e, ...r.events] : r.events));
    setNext(r.next_before_id);
    setLoading(false);
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [filter]);
  const link = (e: HistoryEvent) => (e.entity_type === "item" ? `/items/${e.entity_id}` : e.entity_type === "container" ? `/containers/${e.entity_id}` : e.entity_type === "location" ? `/locations/${e.entity_id}` : e.entity_type === "map" ? `/maps/${e.entity_id}` : null);
  return (
    <div className="page">
      <h1>Activity history</h1>
      <select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ marginBottom: 12 }}>
        <option value="">Everything</option><option value="created">Added</option><option value="moved">Moves</option><option value="quantity_changed">Quantity changes</option>
        <option value="loaned">Loans</option><option value="returned">Returns</option><option value="deleted">Deletions</option><option value="inventory_updated">Photo inventories</option><option value="updated">Edits</option>
      </select>
      <div className="card tight">
        <div className="list">
          {events.map((e) => {
            const to = link(e);
            const body = <div className="grow"><div style={{ fontWeight: 500 }}>{e.summary}</div><div className="s">{e.user || "System"} · {fmtWhen(e.ts)} · {new Date(e.ts).toLocaleString()}</div></div>;
            return to ? <Link key={e.id} to={to}>{body}</Link> : <li key={e.id}>{body}</li>;
          })}
          {!events.length && !loading && <Empty>No activity.</Empty>}
        </div>
      </div>
      {loading && <Spinner />}
      {next && !loading && <button className="btn block" onClick={() => load(next)}>Load more</button>}
    </div>
  );
}

// ---------------------------------------------------------------- loans, temporary, expiry, stock, warranty

export function LoansPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "loans";
  const setTab = (t: string) => setParams({ tab: t }, { replace: true });
  const loans = useApi(() => (tab === "loans" ? api.get<(Loan & { item: Item })[]>("loans") : Promise.resolve([])), [tab]);
  const temp = useApi(() => (tab === "temporary" ? api.get<{ items: Item[]; containers: Container[] }>("temporary") : Promise.resolve(null)), [tab]);
  const exp = useApi(() => (tab === "expiry" ? api.get<Item[]>("expiring", { days: 60 }) : Promise.resolve([])), [tab]);
  const stock = useApi(() => (tab === "stock" ? api.get<LowStock[]>("low-stock") : Promise.resolve([])), [tab]);
  const warr = useApi(() => (tab === "warranty" ? api.get<Item[]>("warranties", { days: 365 }) : Promise.resolve([])), [tab]);
  const { run } = useAction();
  const me = useMe();
  return (
    <div className="page">
      <h1>Loans &amp; temporary</h1>
      <div className="tabs">
        {[["loans", "On loan"], ["temporary", "Moved / in use"], ["expiry", "Expiring"], ["stock", "Stock levels"], ["warranty", "Warranties"]].map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "loans" && (
        <div className="card tight">{loans.loading ? <Spinner /> : loans.data?.length ? (
          <div className="list">{loans.data.map((l) => (
            <div key={l.id} style={{ borderBottom: "1px solid var(--border)" }}>
              <ItemRow item={l.item} right={<div className="s" style={{ textAlign: "right" }}><b style={{ color: "var(--text)" }}>{l.borrower}</b><br />since {fmtDate(l.loaned_at)}{l.expected_return && <><br /><span style={{ color: l.overdue ? "var(--danger)" : undefined }}>due {fmtDate(l.expected_return)}</span></>}</div>} />
              {me.can_edit && <div style={{ padding: "0 16px 10px" }}><button className="btn small" onClick={() => run(() => api.post(`loans/${l.id}/return`, {}), "Marked as returned").then(loans.reload)}><RotateCcw size={14} /> Returned</button></div>}
            </div>
          ))}</div>
        ) : <Empty>Nothing is lent out.</Empty>}</div>
      )}
      {tab === "temporary" && (temp.loading ? <Spinner /> : (
        <>
          <Section title="Items" tight>{temp.data?.items.length ? <div className="list">{temp.data.items.map((i) => <ItemRow key={i.id} item={i} />)}</div> : <Empty>Everything is in its place.</Empty>}</Section>
          {!!temp.data?.containers.length && <Section title="Containers" tight><div className="list">{temp.data.containers.map((c) => <ContainerRow key={c.id} c={c} />)}</div></Section>}
        </>
      ))}
      {tab === "expiry" && <div className="card tight">{exp.loading ? <Spinner /> : exp.data?.length ? <div className="list">{exp.data.map((i) => <ItemRow key={i.id} item={i} right={<div className="s" style={{ textAlign: "right" }}>×{i.quantity}<br />{fmtDate(i.expires_on)}</div>} />)}</div> : <Empty>Nothing expires in the next 60 days.</Empty>}</div>}
      {tab === "stock" && (
        <div className="card tight">{stock.loading ? <Spinner /> : stock.data?.length ? (
          <div className="list">{stock.data.map((p) => (
            <Link key={p.product_id} to={p.item_id ? `/items/${p.item_id}` : "/items"}>
              {p.quantity < (p.min_quantity || 0) ? <AlertTriangle size={20} color="var(--warn)" /> : <span style={{ width: 20 }} />}
              <div className="grow"><div className="t">{p.name}</div><div className="s">Alert below {p.min_quantity}</div></div>
              <div className="qty">{p.quantity}</div>
            </Link>
          ))}</div>
        ) : <Empty>No products have a low-stock alert. Set "Low-stock alert below" on consumables like batteries or light bulbs.</Empty>}</div>
      )}
      {tab === "warranty" && <div className="card tight">{warr.loading ? <Spinner /> : warr.data?.length ? <div className="list">{warr.data.map((i) => <ItemRow key={i.id} item={i} right={<div className="s">{fmtDate(i.warranty_expires)}</div>} />)}</div> : <Empty>No warranties end in the next year.</Empty>}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- settings

interface SettingsData { currency: string; qr_base_url: string; synonyms: Record<string, string[]>; ai: { enabled: boolean; provider?: string; model?: string; error?: string }; barcode_providers: string[]; mode: string; version: string }
interface UserRow { id: number; name: string; username: string | null; ha_is_admin: boolean; role: string; role_override: string | null; last_seen: string }

export function SettingsPage() {
  const me = useMe();
  const s = useApi(() => api.get<SettingsData>("settings"), []);
  const users = useApi(() => (me.is_admin ? api.get<UserRow[]>("users") : Promise.resolve([])), [me.is_admin]);
  const trash = useApi(() => api.get<{ items: Item[]; containers: Container[] }>("trash"), []);
  const [currency, setCurrency] = useState("");
  const [qrBase, setQrBase] = useState("");
  const [syn, setSyn] = useState("");
  const { busy, run } = useAction();
  const toast = useToast();
  useEffect(() => {
    if (s.data) {
      setCurrency(s.data.currency);
      setQrBase(s.data.qr_base_url);
      setSyn(Object.entries(s.data.synonyms || {}).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n"));
    }
  }, [s.data]);
  if (!s.data) return s.error ? <div className="page"><ErrorBox error={s.error} /></div> : <Spinner />;

  async function saveSettings() {
    const synonyms: Record<string, string[]> = {};
    for (const line of syn.split("\n")) {
      const [k, v] = line.split(":");
      if (k?.trim() && v) synonyms[k.trim().toLowerCase()] = v.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    }
    await run(() => api.patch("settings", { currency: currency.toUpperCase(), qr_base_url: qrBase.trim(), synonyms }), "Settings saved");
  }

  return (
    <div className="page">
      <h1>Settings</h1>
      <Section title="You">
        <p>Signed in through Home Assistant as <b>{me.name}</b> ({me.role.replace("_", "-")}).</p>
        <p className="muted" style={{ margin: 0 }}>Home Inventory {s.data.version}</p>
      </Section>
      <Section title="AI">
        {s.data.ai.enabled ? <p>Using <b>{s.data.ai.provider === "home_assistant" ? "Home Assistant AI Task" : s.data.ai.provider}</b>{s.data.ai.model && s.data.ai.model !== "auto" ? ` (${s.data.ai.model})` : ""}.</p> : <p>AI is off{s.data.ai.error ? `: ${s.data.ai.error}` : ""}. Everything works without it; photo identification and smart search are unavailable.</p>}
        <p className="muted" style={{ margin: 0 }}>Change the provider in Home Assistant → Settings → Apps → Home Inventory → Configuration.</p>
      </Section>
      <Section title="Barcode lookups">
        <p style={{ margin: 0 }}>{s.data.barcode_providers.length ? `Enabled: ${s.data.barcode_providers.join(", ")}` : "External lookups are disabled; only your own inventory is searched."}</p>
      </Section>
      {me.is_admin && (
        <Section title="Preferences">
          <div className="stack">
            <label className="field">Currency<input maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} style={{ width: 100 }} /></label>
            <label className="field">QR label link (optional)
              <span className="hint">Your Home Assistant address for this app, e.g. https://ha.example.com/hassio/ingress/xxxx_home_inventory — lets a phone camera open labels directly. Labels always work with the in-app scanner.</span>
              <input value={qrBase} onChange={(e) => setQrBase(e.target.value)} placeholder="https://…" />
            </label>
            <label className="field">Search synonyms
              <span className="hint">One per line: word: alternative, alternative</span>
              <textarea rows={4} value={syn} onChange={(e) => setSyn(e.target.value)} placeholder="loft: attic" />
            </label>
            <button className="btn primary" disabled={busy} onClick={saveSettings}>Save</button>
          </div>
        </Section>
      )}
      {me.is_admin && (
        <Section title="Household members" tight>
          <p className="muted" style={{ padding: "0 16px" }}>Everyone signs in with their Home Assistant account. Home Assistant administrators are administrators here; others are members unless you change it.</p>
          <div className="list">
            {users.data?.map((u) => (
              <li key={u.id}>
                <div className="grow"><div className="t">{u.name}</div><div className="s">{u.ha_is_admin ? "HA administrator · " : ""}last seen {fmtWhen(u.last_seen)}</div></div>
                <select value={u.role_override || ""} style={{ width: "auto" }} disabled={u.id === me.id} onChange={(e) => run(() => api.patch(`users/${u.id}`, { role: e.target.value || null }), "Role updated").then(users.reload)}>
                  <option value="">Automatic ({u.role.replace("_", "-")})</option><option value="admin">Administrator</option><option value="member">Member</option><option value="read_only">Read-only</option>
                </select>
              </li>
            ))}
          </div>
        </Section>
      )}
      <Section title="Trash" tight>
        {trash.data && (trash.data.items.length || trash.data.containers.length) ? (
          <div className="list">
            {trash.data.containers.map((c) => <li key={`c${c.id}`}><div className="grow"><div className="t">{c.name}</div><div className="s">container · deleted {fmtWhen(c.deleted_at!)}</div></div>{me.can_edit && <button className="btn small" onClick={() => run(() => api.post(`containers/${c.id}/restore`), "Restored").then(trash.reload)}>Restore</button>}</li>)}
            {trash.data.items.map((i) => (
              <li key={i.id}>
                <div className="grow"><Link className="t" to={`/items/${i.id}`}>{i.display_name}</Link><div className="s">×{i.quantity} · deleted {fmtWhen(i.deleted_at!)}</div></div>
                {me.can_edit && <button className="btn small" onClick={() => run(() => api.post(`items/${i.id}/restore`), "Restored").then(trash.reload)}>Restore</button>}
                {me.is_admin && <button className="btn small danger" onClick={() => window.confirm("Permanently delete? This cannot be undone.") && run(() => api.del(`trash/items/${i.id}`), "Deleted permanently").then(trash.reload)}>Purge</button>}
              </li>
            ))}
          </div>
        ) : <Empty>Trash is empty.</Empty>}
      </Section>
      {me.is_admin && (
        <Section title="Backup & restore">
          <p>Your inventory (database, photos and documents) is included automatically in every Home Assistant backup. You can also download a portable archive here.</p>
          <div className="row wrap">
            <a className="btn" href={url("api/v1/backup/export")} download><Download size={18} /> Download backup</a>
            <label className="btn danger">
              <Upload size={18} /> Restore from file…
              <input type="file" accept=".zip,application/zip" hidden onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f || !window.confirm("Replace ALL current inventory data with this backup? A safety copy of the current data is kept in the app's backups folder.")) return;
                const fd = new FormData();
                fd.append("file", f);
                const r = await run(() => api.upload<{ safety_backup: string }>("backup/restore", fd));
                if (r) { toast(`Restored. Previous data saved as ${r.safety_backup}`); window.setTimeout(() => window.location.reload(), 1500); }
              }} />
            </label>
          </div>
        </Section>
      )}
    </div>
  );
}
