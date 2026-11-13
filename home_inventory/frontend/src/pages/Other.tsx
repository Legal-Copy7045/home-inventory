import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, Download, Printer, RotateCcw, Upload } from "lucide-react";
import { api, appBaseUrl, url, type Container, type FlatLocation, type HistoryEvent, type Item, type Loan, type LowStock } from "../api";
import { ContainerRow, Empty, ErrorBox, ItemRow, Section, Spinner, fmtDate, fmtWhen, useAction, useApi, useMe, useToast } from "../components/ui";

// ---------------------------------------------------------------- labels

export function LabelsPage() {
  const me = useMe();
  const [params] = useSearchParams();
  const all = useApi(() => api.get<Container[]>("containers"), []);
  const locs = useApi(() => api.get<FlatLocation[]>("locations"), []);
  const ids = (k: string) => new Set((params.get(k) || "").split(",").filter(Boolean).map(Number));
  const [sel, setSel] = useState<Set<number>>(ids("ids"));
  const [locSel, setLocSel] = useState<Set<number>>(ids("locs"));
  const [tab, setTab] = useState<"containers" | "locations">(params.get("locs") && !params.get("ids") ? "locations" : "containers");
  const [template, setTemplate] = useState(() => localStorageGet("labelTemplate") || me.app.label_templates[0]?.key || "avery_l7160");
  // Shelf labels opened from a location list what's kept there by default.
  const [contents, setContents] = useState(!!params.get("locs") && !params.get("ids"));
  // Optional arrow on each label pointing to the shelf/box: key "C<id>" or "L<id>".
  const [arrows, setArrows] = useState<Record<string, string>>({});
  const setArrow = (key: string, v: string) => setArrows((a) => { const n = { ...a }; if (v) n[key] = v; else delete n[key]; return n; });
  const setAllArrows = (v: string) => {
    const keys = [...[...sel].map((id) => `C${id}`), ...[...locSel].map((id) => `L${id}`)];
    setArrows((a) => { const n = { ...a }; keys.forEach((k) => { if (v) n[k] = v; else delete n[k]; }); return n; });
  };
  const [location, setLocation] = useState(true);
  const [copies, setCopies] = useState(1);
  const [start, setStart] = useState(1);
  const { busy, run } = useAction();
  useEffect(() => localStorageSet("labelTemplate", template), [template]);
  const toggleIn = (set: Set<number>, apply: (s: Set<number>) => void, id: number) => { const s = new Set(set); if (s.has(id)) s.delete(id); else s.add(id); apply(s); };
  const total = sel.size + locSel.size;

  async function print() {
    const blob = await run(() => api.blob("POST", "labels/pdf", { container_ids: [...sel], location_ids: [...locSel], base_url: appBaseUrl(), template, include_contents: contents, include_location: location, copies, start_position: start, arrows: Object.fromEntries(Object.entries(arrows).filter(([k]) => (k[0] === "C" ? sel : locSel).has(Number(k.slice(1))))) }));
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
        <label className="check"><input type="checkbox" checked={contents} onChange={(e) => setContents(e.target.checked)} /> <span>Print contents list<span className="hint" style={{ display: "block" }}>For a shelf: the places, boxes and items kept on it, so things go back where they belong.</span></span></label>
        <label className="field">Arrow on the selected labels
          <span className="hint">Points to where the shelf or box is, e.g. a label on the end of a rack pointing up to the top shelf. You can also set one per label below.</span>
          <select value="" onChange={(e) => { setAllArrows(e.target.value); e.target.value = ""; }} disabled={!total} style={{ width: "auto" }}>
            <option value="" disabled>Set for all selected…</option>
            <option value="">No arrow</option>
            {ARROW_CHOICES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
      </div>
      <div className="tabs">
        <button className={tab === "containers" ? "active" : ""} onClick={() => setTab("containers")}>Containers{sel.size ? ` (${sel.size})` : ""}</button>
        <button className={tab === "locations" ? "active" : ""} onClick={() => setTab("locations")}>Locations{locSel.size ? ` (${locSel.size})` : ""}</button>
      </div>
      <div className="row between" style={{ margin: "8px 0" }}>
        <b>{total} label{total === 1 ? "" : "s"} selected</b>
        <div className="row">
          <button className="btn small" onClick={() => (tab === "containers" ? setSel(new Set(all.data?.map((c) => c.id))) : setLocSel(new Set(locs.data?.map((l) => l.id))))}>All</button>
          <button className="btn small" onClick={() => (tab === "containers" ? setSel(new Set()) : setLocSel(new Set()))}>None</button>
        </div>
      </div>
      <div className="card tight">
        {tab === "containers" && (all.loading ? <Spinner /> : (
          <div className="list">
            {all.data?.map((c) => (
              <label key={c.id} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggleIn(sel, setSel, c.id)} />
                <div className="grow" style={{ flex: 1 }}><b>{c.number ? `#${c.number} ` : ""}{c.name}</b><div className="muted" style={{ fontSize: ".85rem" }}>{c.placement?.path || "Unassigned"}</div></div>
                {sel.has(c.id) ? <ArrowPick value={arrows[`C${c.id}`] || ""} onChange={(v) => setArrow(`C${c.id}`, v)} /> : <span className="mono muted">{c.qr_code}</span>}
              </label>
            ))}
            {!all.data?.length && <Empty>Create containers first.</Empty>}
          </div>
        ))}
        {tab === "locations" && (locs.loading ? <Spinner /> : (
          <div className="list">
            {locs.data?.map((l) => (
              <label key={l.id} className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                <input type="checkbox" checked={locSel.has(l.id)} onChange={() => toggleIn(locSel, setLocSel, l.id)} />
                <div className="grow" style={{ flex: 1 }}><b>{l.name}</b><div className="muted" style={{ fontSize: ".85rem" }}>{l.path}</div></div>
                {locSel.has(l.id) ? <ArrowPick value={arrows[`L${l.id}`] || ""} onChange={(v) => setArrow(`L${l.id}`, v)} /> : <span className="mono muted">{l.qr_code}</span>}
              </label>
            ))}
            {!locs.data?.length && <Empty>Create locations first.</Empty>}
          </div>
        ))}
      </div>
      {!appBaseUrl() && !me.app.qr_base_url && <div className="notice" style={{ marginTop: 10 }}>Open this page from the Home Assistant sidebar (or set the QR label link in Settings) so labels open in your phone's camera app.</div>}
      <button className="btn primary big block" disabled={!total || busy} onClick={print}><Printer size={20} /> Create PDF</button>
    </div>
  );
}

const ARROW_CHOICES: [string, string][] = [
  ["up", "↑ Up"], ["down", "↓ Down"], ["left", "← Left"], ["right", "→ Right"],
  ["up_left", "↖ Up left"], ["up_right", "↗ Up right"], ["down_left", "↙ Down left"], ["down_right", "↘ Down right"],
];

/** Compact arrow choice for one label. */
function ArrowPick({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select aria-label="Arrow" value={value} onClick={(e) => e.stopPropagation()} onChange={(e) => onChange(e.target.value)} style={{ width: "auto", padding: "4px 6px" }}>
      <option value="">No arrow</option>
      {ARROW_CHOICES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
    </select>
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

interface SettingsData { currency: string; qr_base_url: string; amazon_domain: string | null; amazon_associate_tag: string | null; synonyms: Record<string, string[]>; ai: { enabled: boolean; provider?: string; model?: string; error?: string; usage?: { requests_last_minute: number; requests_last_24h: number; limits: { per_minute: number; tokens_per_minute: number; per_day: number }; paused_for: number } | null }; barcode_providers: string[]; mode: string; version: string; maintenance_auto_shopping: boolean; hemisphere: "north" | "south" }
interface UserRow { id: number; name: string; username: string | null; ha_is_admin: boolean; role: string; role_override: string | null; last_seen: string }

export function SettingsPage() {
  const me = useMe();
  const s = useApi(() => api.get<SettingsData>("settings"), []);
  const users = useApi(() => (me.is_admin ? api.get<UserRow[]>("users") : Promise.resolve([])), [me.is_admin]);
  const trash = useApi(() => api.get<{ items: Item[]; containers: Container[] }>("trash"), []);
  const [currency, setCurrency] = useState("");
  const [qrBase, setQrBase] = useState("");
  const [syn, setSyn] = useState("");
  const [amazonDomain, setAmazonDomain] = useState("");
  const [amazonTag, setAmazonTag] = useState("");
  const [autoShop, setAutoShop] = useState(true);
  const [hemi, setHemi] = useState<"north" | "south">("north");
  const { busy, run } = useAction();
  const toast = useToast();
  useEffect(() => {
    if (s.data) {
      setCurrency(s.data.currency);
      setQrBase(s.data.qr_base_url);
      setAmazonDomain(s.data.amazon_domain || "");
      setAmazonTag(s.data.amazon_associate_tag || "");
      setAutoShop(s.data.maintenance_auto_shopping !== false);
      setHemi(s.data.hemisphere || "north");
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
    await run(() => api.patch("settings", {
      currency: currency.toUpperCase(), qr_base_url: qrBase.trim(), synonyms,
      amazon_domain: amazonDomain.trim() || null, amazon_associate_tag: amazonTag.trim() || null,
      maintenance_auto_shopping: autoShop, hemisphere: hemi,
    }), "Settings saved");
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
        {s.data.ai.usage && (
          <p style={{ marginTop: 0 }}>
            Used {s.data.ai.usage.requests_last_24h}{s.data.ai.usage.limits.per_day ? ` of ${s.data.ai.usage.limits.per_day}` : ""} AI requests in the last 24 hours
            {s.data.ai.usage.limits.per_minute ? ` · limit ${s.data.ai.usage.limits.per_minute} a minute` : ""}.
            {s.data.ai.usage.paused_for > 0 ? ` Paused for ${s.data.ai.usage.paused_for} s because the AI service asked to slow down.` : ""}
            {" "}Requests queue so these limits are never exceeded.
          </p>
        )}
        <p className="muted" style={{ margin: 0 }}>Change the provider and its limits in Home Assistant → Settings → Apps → Home Inventory → Configuration.</p>
      </Section>
      <Section title="Barcode lookups">
        <p style={{ margin: 0 }}>{s.data.barcode_providers.length ? `Enabled: ${s.data.barcode_providers.join(", ")}` : "External lookups are disabled; only your own inventory is searched."}</p>
      </Section>
      {me.is_admin && (
        <Section title="Preferences">
          <div className="stack">
            <label className="field">Currency<input maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} style={{ width: 100 }} /></label>
            <label className="field">QR label link (optional)
              <span className="hint">Leave empty to use the address you opened Home Assistant with{appBaseUrl() ? ` (${appBaseUrl()})` : ""}. Set it if labels should open a different address, e.g. your external one.</span>
              <input value={qrBase} onChange={(e) => setQrBase(e.target.value)} placeholder={appBaseUrl() || "https://…"} />
            </label>
            <label className="field">Search synonyms
              <span className="hint">One per line: word: alternative, alternative</span>
              <textarea rows={4} value={syn} onChange={(e) => setSyn(e.target.value)} placeholder="loft: attic" />
            </label>
            <label className="field">Amazon store
              <span className="hint">Used for product links and the "add to cart" hand-off, e.g. amazon.com or amazon.co.uk.</span>
              <input value={amazonDomain} onChange={(e) => setAmazonDomain(e.target.value)} placeholder="amazon.com" />
            </label>
            <label className="field">Amazon Associates tag (optional)
              <span className="hint">Only if you have your own Associates account; added to links you open.</span>
              <input value={amazonTag} onChange={(e) => setAmazonTag(e.target.value)} />
            </label>
            <label className="row" style={{ gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={autoShop} onChange={(e) => setAutoShop(e.target.checked)} />
              <span>Add missing maintenance parts to the shopping list
                <span className="hint" style={{ display: "block" }}>When equipment becomes due for service, parts it needs that aren't in stock are added to Restock (once per service).</span>
              </span>
            </label>
            <label className="field">Seasons
              <span className="hint">For the seasonal maintenance checklist.</span>
              <select value={hemi} onChange={(e) => setHemi(e.target.value as "north" | "south")} style={{ width: "auto" }}>
                <option value="north">Northern hemisphere (winter in December)</option>
                <option value="south">Southern hemisphere (winter in June)</option>
              </select>
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
