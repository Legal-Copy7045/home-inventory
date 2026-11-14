import { Link, useSearchParams } from "react-router-dom";
import { CalendarClock, ClipboardList, FileText, Leaf, Printer, ShieldCheck, ShoppingCart, Wrench } from "lucide-react";
import { api } from "../api";
import { ErrorBox, Spinner, fmtDate, useAction, useApi, useMe } from "../components/ui";
import SortPhotos from "./SortPhotos";
import ToFinish from "./ToFinish";
import { MaintenancePage } from "./Maintenance";
import { CustomersPage, RepairsPage } from "./Customers";

/** Tabs above a page that already has its own content. */
function HubTabs<T extends string>({ tabs, current }: { tabs: [T, string][]; current: T }) {
  const [params, setParams] = useSearchParams();
  return (
    <div className="page" style={{ paddingBottom: 0 }}>
      <div className="tabs" style={{ marginBottom: 0 }}>
        {tabs.map(([k, label]) => (
          <button key={k} className={current === k ? "active" : ""} onClick={() => { const p = new URLSearchParams(params); p.set("tab", k); setParams(p, { replace: true }); }}>{label}</button>
        ))}
      </div>
    </div>
  );
}

/** To sort (quick photos without a place) and To finish (items missing details), together. */
export function TodoPage() {
  const [params] = useSearchParams();
  const tab = params.get("tab") === "finish" ? "finish" : "sort";
  return (
    <>
      <HubTabs tabs={[["sort", "To sort"], ["finish", "To finish"]]} current={tab} />
      {tab === "sort" ? <SortPhotos /> : <ToFinish />}
    </>
  );
}

/** Maintenance and repairs, for your own equipment and other people's. */
export function CarePage() {
  const me = useMe();
  const [params] = useSearchParams();
  const want = params.get("tab");
  const tabs: ["maintenance" | "repairs" | "customers", string][] = [["maintenance", "Maintenance"]];
  if (me.can_edit) tabs.push(["repairs", "Repairs"], ["customers", "Customers"]);
  const tab = tabs.some(([k]) => k === want) ? (want as "maintenance" | "repairs" | "customers") : "maintenance";
  return (
    <>
      <HubTabs tabs={tabs} current={tab} />
      {tab === "maintenance" ? <MaintenancePage /> : tab === "repairs" ? <RepairsPage /> : <CustomersPage />}
    </>
  );
}

/** Every report in one place. */
export function ReportsPage() {
  const reports: { to: string; icon: typeof FileText; title: string; text: string }[] = [
    { to: "/report", icon: FileText, title: "Inventory report", text: "Everything you own by room, with values, serial numbers and paperwork. Print it or download CSV / Excel; estimate values with AI." },
    { to: "/care?tab=maintenance", icon: CalendarClock, title: "Upcoming maintenance", text: "What's overdue and due soon, this season's jobs, and parts needed." },
    { to: "/reports/season", icon: Leaf, title: "Season mode", text: "Spring get-ready or autumn winterising: every machine's jobs, the parts to buy in one go, fuel and oils, and hour meters to photograph." },
    { to: "/reports/maintenance-plan", icon: Wrench, title: "Maintenance plan & guide", text: "Every machine with its jobs, how often, when they're next due, instructions and parts. Printable." },
    { to: "/shopping", icon: ShoppingCart, title: "Restock & shopping list", text: "What's running low, and the shopping list with where to buy." },
    { to: "/loans?tab=warranty", icon: ShieldCheck, title: "Warranties & expiry", text: "Warranties ending soon and things to use before they expire." },
    { to: "/loans", icon: ClipboardList, title: "Loans & temporary", text: "What's lent out, to whom, and what's away from its usual place." },
  ];
  return (
    <div className="page">
      <h1>Reports</h1>
      <div className="list card tight">
        {reports.map((r) => (
          <Link key={r.to} to={r.to}>
            <r.icon size={22} />
            <div className="grow"><div className="t">{r.title}</div><div className="s">{r.text}</div></div>
          </Link>
        ))}
      </div>
    </div>
  );
}

interface PlanTask {
  id: number; title: string; instructions: string | null; status: string;
  interval_days: number | null; interval_hours: number | null; interval_cycles: number | null;
  next_due_on: string | null; next_due_hours: number | null; days_left: number | null; hours_left: number | null;
  estimated_due_on: string | null;
  parts: { name: string; quantity: number; unit: string | null; status: string }[];
}
interface PlanEntry {
  item: { id: number; name: string; label: string | null; manufacturer: string | null; model_number: string | null; place: string };
  profile: { equipment_type: string | null; specs?: { label: string; value: string; part_number?: string }[] } | null;
  meters: { hours: number | null; cycles: number | null };
  per_week: { hours: number | null; cycles: number | null };
  tasks: PlanTask[];
  last_service: { title: string; performed_on: string } | null;
}

const every = (t: PlanTask) => [t.interval_days && `${t.interval_days % 365 === 0 ? `${t.interval_days / 365} year${t.interval_days === 365 ? "" : "s"}` : `${t.interval_days} days`}`,
  t.interval_hours && `${t.interval_hours} h`, t.interval_cycles && `${t.interval_cycles} uses`].filter(Boolean).join(" or ") || "when needed";
const due = (t: PlanTask) => t.status === "overdue" ? "Overdue" : [t.next_due_on && fmtDate(t.next_due_on), t.next_due_hours !== null && `${t.next_due_hours} h`,
  t.estimated_due_on && `≈ ${fmtDate(t.estimated_due_on)} at your usage`].filter(Boolean).join(" · ") || "—";

/** Printable plan: each machine, its jobs and what they need. */
export function MaintenancePlanPage() {
  const plan = useApi(() => api.get<{ equipment: PlanEntry[] }>("maintenance/plan"), []);
  if (!plan.data) return plan.error ? <div className="page"><ErrorBox error={plan.error} /></div> : <Spinner />;
  const eq = plan.data.equipment;
  const upcoming = eq.flatMap((e) => e.tasks.filter((t) => t.status === "overdue" || t.status === "due_soon").map((t) => ({ e, t })));
  return (
    <div className="page report">
      <div className="row between wrap no-print">
        <h1 style={{ margin: 0 }}>Maintenance plan &amp; guide</h1>
        <button className="btn small" onClick={() => window.print()}><Printer size={16} /> Print / PDF</button>
      </div>
      <p className="muted print-only">Generated {new Date().toLocaleString()}</p>
      {!eq.length && <div className="card"><div className="empty">No equipment is tracked yet. Open an item such as a mower and tap <b>Manage as equipment</b>.</div></div>}
      {upcoming.length > 0 && (
        <section className="card tight">
          <div className="card-title" style={{ padding: "14px 16px 0" }}><h2>Due now and soon</h2></div>
          <ul className="list">
            {upcoming.map(({ e, t }) => (
              <li key={t.id}><div className="grow"><div className="t">{e.item.name} – {t.title}</div><div className="s">{due(t)}{t.parts.some((p) => p.status === "missing" || p.status === "partial") ? " · parts needed" : ""}</div></div>
                <span className={`badge ${t.status === "overdue" ? "danger" : "warn"}`}>{t.status === "overdue" ? "Overdue" : "Due soon"}</span></li>
            ))}
          </ul>
        </section>
      )}
      {eq.map((e) => (
        <section key={e.item.id} className="card report-room">
          <div className="card-title">
            <h2><Link to={`/items/${e.item.id}`}>{e.item.name}</Link></h2>
            <span className="muted">{e.profile?.equipment_type || "Equipment"}</span>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            {[e.item.manufacturer, e.item.model_number].filter(Boolean).join(" ")}{e.item.manufacturer || e.item.model_number ? " · " : ""}{e.item.place}
            {e.meters.hours !== null ? ` · ${e.meters.hours} h on the meter` : ""}{e.per_week.hours ? ` (about ${e.per_week.hours} h a week)` : ""}
            {e.last_service ? ` · last service ${fmtDate(e.last_service.performed_on)}: ${e.last_service.title}` : " · no service recorded"}
          </p>
          {!!e.profile?.specs?.filter((s) => s.value).length && (
            <p style={{ marginTop: 0 }}><b>Specifications:</b> {e.profile.specs.filter((s) => s.value).map((s) => `${s.label}: ${s.value}${s.part_number ? ` (${s.part_number})` : ""}`).join(" · ")}</p>
          )}
          <table className="plan-table">
            <thead><tr><th>Job</th><th>Every</th><th>Next due</th><th>Parts</th></tr></thead>
            <tbody>
              {e.tasks.map((t) => (
                <tr key={t.id}>
                  <td><b>{t.title}</b>{t.instructions && <div className="muted" style={{ whiteSpace: "pre-line" }}>{t.instructions}</div>}</td>
                  <td>{every(t)}</td>
                  <td>{due(t)}</td>
                  <td>{t.parts.length ? t.parts.map((p) => `${p.quantity}${p.unit ? ` ${p.unit}` : " ×"} ${p.name}${p.status === "missing" ? " (none in stock)" : p.status === "partial" ? " (not enough)" : ""}`).join("; ") : "—"}</td>
                </tr>
              ))}
              {!e.tasks.length && <tr><td colSpan={4} className="muted">No jobs set up.</td></tr>}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

interface SeasonJob { id: number; title: string; status: string; next_due_on: string | null; parts?: { name: string; status: string }[] }
interface Consumable { need: string; machines: string[]; have: { item_id: number; name: string; quantity: number }[]; status: "ok" | "low" | "none" }
interface SeasonData {
  season: "spring" | "autumn";
  machines: { item: { id: number; name: string; place: string }; fuel_type: string | null; jobs: SeasonJob[] }[];
  parts_to_get: { name: string; quantity: number; unit: string | null; for: string[] }[];
  consumables: Consumable[];
}

/** Season mode: get the equipment ready for spring, or put it away for winter. */
export function SeasonPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const which = params.get("season") as "spring" | "autumn" | null;
  const d = useApi(() => api.get<SeasonData>("maintenance/season", which ? { season: which } : undefined), [which]);
  const readings = useApi(() => api.get<{ machines: { item_id: number; name: string; days: number | null }[] }>("maintenance/readings-due"), []);
  const { busy, run } = useAction();
  if (!d.data) return d.error ? <div className="page"><ErrorBox error={d.error} /></div> : <Spinner />;
  const s = d.data;
  const label = s.season === "spring" ? "Spring: get ready" : "Autumn: put away for winter";
  return (
    <div className="page">
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}>{label}</h1>
        <div className="tabs" style={{ margin: 0 }}>
          {(["spring", "autumn"] as const).map((x) => <button key={x} className={s.season === x ? "active" : ""} onClick={() => setParams({ season: x }, { replace: true })}>{x === "spring" ? "Spring" : "Autumn"}</button>)}
        </div>
      </div>
      <p className="muted">{s.season === "spring" ? "Jobs to do before the mowing season, and what to buy for them." : "Winterising jobs (fuel stabiliser, running dry, batteries in) and what they need."} Anything overdue is included too.</p>

      {s.consumables.length > 0 && (
        <section className="card tight" style={{ marginBottom: 12 }}>
          <div className="card-title" style={{ padding: "14px 16px 0" }}><h2>Fuel &amp; oils</h2></div>
          <div className="list">
            {s.consumables.map((c) => (
              <li key={c.need}>
                <div className="grow">
                  <div className="t">{c.need} {c.status !== "ok" && <span className={`badge ${c.status === "none" ? "danger" : "warn"}`}>{c.status === "none" ? "none in stock" : "running low"}</span>}</div>
                  <div className="s">For {c.machines.join(", ")}</div>
                  <div className="s">{c.have.length ? c.have.map((h) => <Link key={h.item_id} to={`/items/${h.item_id}`} style={{ marginRight: 8 }}>{h.name} ({h.quantity})</Link>) : "Not in your inventory"}</div>
                </div>
              </li>
            ))}
          </div>
        </section>
      )}

      {s.parts_to_get.length > 0 && (
        <section className="card" style={{ marginBottom: 12 }}>
          <div className="row between wrap"><h2 style={{ margin: 0 }}>Parts to get</h2>
            {me.can_edit && <button className="btn small primary" disabled={busy} onClick={async () => {
              const r = await run(() => api.post<{ added: string[] }>(`maintenance/season/shopping?season=${s.season}`));
              if (r) d.reload();
            }}><ShoppingCart size={16} /> Add all to the shopping list</button>}
          </div>
          <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>
            {s.parts_to_get.map((p) => <li key={p.name}><b>{p.quantity}{p.unit ? ` ${p.unit}` : ""} × {p.name}</b> <span className="muted">— {p.for.join("; ")}</span></li>)}
          </ul>
        </section>
      )}

      {(readings.data?.machines.length ?? 0) > 0 && (
        <section className="card" style={{ marginBottom: 12 }}>
          <h2 style={{ marginTop: 0 }}>Hour meters to photograph</h2>
          <p className="muted">These have no sensor; a photo of the hour meter keeps their hour-based jobs right.</p>
          {readings.data!.machines.map((m) => <Link key={m.item_id} className="btn small" style={{ marginRight: 6, marginBottom: 6 }} to={`/items/${m.item_id}`}>{m.name} · {m.days === null ? "never read" : `${m.days} days ago`}</Link>)}
        </section>
      )}

      {s.machines.length ? s.machines.map((m) => (
        <section key={m.item.id} className="card tight" style={{ marginBottom: 10 }}>
          <div className="card-title" style={{ padding: "14px 16px 0" }}>
            <h2><Link to={`/items/${m.item.id}`}>{m.item.name}</Link></h2>
            <span className="muted">{m.item.place}{m.fuel_type ? ` · ${m.fuel_type}` : ""}</span>
          </div>
          <div className="list">
            {m.jobs.map((j) => (
              <li key={j.id}>
                <div className="grow">
                  <div className="t">{j.title} {j.status === "overdue" && <span className="badge danger">overdue</span>}{j.status === "due_soon" && <span className="badge warn">due soon</span>}</div>
                  {j.parts && j.parts.length > 0 && <div className="s">Needs: {j.parts.map((p) => `${p.name}${p.status === "in_stock" ? "" : " (to get)"}`).join(", ")}</div>}
                </div>
              </li>
            ))}
          </div>
        </section>
      )) : <div className="card"><div className="empty">No equipment with {s.season} jobs. Manage a machine as equipment to see it here.</div></div>}
    </div>
  );
}
