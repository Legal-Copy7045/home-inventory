// The Equipment section: things you *manage* (a mower, a generator), on the
// same item records as the rest of the inventory, with their own pages.
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, BookOpen, Check, ClipboardList, FileText, Gauge, Package, Printer, ShieldCheck, ShoppingCart, Tractor } from "lucide-react";
import { api, url, type EquipmentProfile, type Item, type MaintenanceSummary, type MaintTask, type ReadinessLine, type ServiceEntry } from "../api";
import { EquipmentSection, ServiceSheet, TaskBadge, TaskSheet, dueText } from "./Maintenance";
import { ManualSheet, ReadyBadge, TaskPartsSheet } from "../components/Parts";
import { Empty, ErrorBox, Section, Spinner, Thumb, fmtDate, money, useAction, useApi, useMe } from "../components/ui";

type Attention = "overdue" | "due_soon" | "ok";
interface EquipmentRow {
  id: number; name: string; label: string | null; manufacturer: string | null; model_number: string | null; serial_number: string | null;
  equipment_type: string | null; lifecycle_status: string; lifecycle_label: string; hours: number | null; placement: string;
  photo_url: string | null; last_service: { on: string; title: string } | null; plan: PlanState;
  overdue: number; due_soon: number; tasks: number; attention: Attention;
  next: { id: number; title: string; status: string; next_due_on: string | null; hours_left: number | null; days_left: number | null } | null;
}
type PlanState = "from_manual" | "from_manual_confirmed" | "generic" | "manual_not_read" | "no_manual";
interface PartLine extends Omit<ReadinessLine, "sources" | "tasks"> {
  needed_soon: boolean;
  tasks: { id: number; title: string; status: string }[];
  sources: { product: ReadinessLine["product"]; sure: boolean; why: string; count: number; amount: number | null;
    items: { id: number; quantity: number; label: string | null; where: string | null }[] }[];
}
interface Overview {
  item: Item; profile: EquipmentProfile; lifecycle_label: string;
  meters: { hours: number | null; cycles: number | null };
  summary: { overdue: number; due_soon: number; tasks: number; attention: Attention; next: EquipmentRow["next"] };
  upcoming: MaintTask[]; last_completed: ServiceEntry | null; recent: ServiceEntry[];
  plan: { state: PlanState; manuals: { id: number; name: string | null; url: string }[]; applied_reading_id: number | null; needs_review: number };
  parts: PartLine[]; costs: { services: number; labor: number; parts: number; total: number };
  warranty: { expires: string; active: boolean; days_left: number } | null; entities: string[];
}
interface Checklist { groups: { key: string; label: string; tasks: (MaintTask & { parts: ReadinessLine[] })[] }[]; meters: { hours: number | null } }

const PLAN_TEXT: Record<PlanState, string> = {
  from_manual: "Maintenance plan from the manufacturer's manual",
  from_manual_confirmed: "Plan from a manual you confirmed fits this model",
  generic: "Generic plan: a typical schedule, not this model's manual",
  manual_not_read: "A manual is attached but its schedule hasn't been read in",
  no_manual: "No manufacturer's manual on file",
};
const EVERY: Record<string, string> = { before_use: "Every time, before use", after_use: "Every time, after use", storage: "Before storing it", seasonal: "Each season" };
const ATTN: Record<Attention, { text: string; color: string }> = {
  overdue: { text: "Overdue", color: "var(--danger)" },
  due_soon: { text: "Due soon", color: "var(--warn)" },
  ok: { text: "Up to date", color: "var(--ok, #2e7d32)" },
};

function AttnBadge({ a, n }: { a: Attention; n?: number }) {
  return <span className="badge" style={{ background: ATTN[a].color, color: "#fff" }}>{n ? `${n} ` : ""}{ATTN[a].text.toLowerCase()}</span>;
}

function nextText(n: EquipmentRow["next"]): string {
  if (!n) return "Nothing scheduled";
  const bits = [];
  if (n.hours_left !== null) bits.push(n.hours_left <= 0 ? `${Math.abs(n.hours_left)} h overdue` : `in ${n.hours_left} h`);
  if (n.days_left !== null) bits.push(n.days_left < 0 ? `${-n.days_left} days overdue` : n.days_left === 0 ? "today" : `in ${n.days_left} days`);
  return `${n.title}${bits.length ? ` (${bits.join(" or ")})` : ""}`;
}

// ---------------------------------------------------------------- list

export function EquipmentListPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const type = params.get("type") || "";
  const attention = params.get("attention") || "";
  const lifecycle = params.get("lifecycle") || "";
  const list = useApi(() => api.get<{ equipment: EquipmentRow[]; types: string[] | null; lifecycle: Record<string, string> }>("equipment",
    { q, equipment_type: type, attention, lifecycle }), [q, type, attention, lifecycle]);
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }); };
  const [types, setTypes] = useState<string[]>([]);
  if (list.data?.types && list.data.types.join() !== types.join()) setTypes(list.data.types);
  const rows = list.data?.equipment || [];
  const totals = { overdue: rows.filter((r) => r.attention === "overdue").length, due: rows.filter((r) => r.attention === "due_soon").length };
  return (
    <div className="page">
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}><Tractor size={24} style={{ verticalAlign: "-4px" }} /> Equipment</h1>
        <Link className="btn small" to="/maintenance/reports"><ClipboardList size={16} /> Maintenance reports</Link>
      </div>
      <p className="muted">Things you look after: their maintenance, manuals, parts and history. Ordinary supplies (oil, plugs, filters) stay in your inventory. To add equipment, open an item and choose <b>Manage as equipment</b>.</p>
      {list.data && rows.length > 0 && !q && !attention && (
        <div className="row wrap" style={{ gap: 8, marginBottom: 10 }}>
          {totals.overdue > 0 && <button className="btn small" onClick={() => set("attention", "overdue")}><AlertTriangle size={14} color="var(--danger)" /> {totals.overdue} with overdue work</button>}
          {totals.due > 0 && <button className="btn small" onClick={() => set("attention", "due_soon")}>{totals.due} due soon</button>}
        </div>
      )}
      <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
        <input className="grow" type="search" placeholder="Search equipment: name, make, model, serial" value={q} onChange={(e) => set("q", e.target.value)} />
        {types.length > 1 && <select value={type} onChange={(e) => set("type", e.target.value)} style={{ width: "auto" }}><option value="">All types</option>{types.map((t) => <option key={t}>{t}</option>)}</select>}
        <select value={attention} onChange={(e) => set("attention", e.target.value)} style={{ width: "auto" }} aria-label="Needs attention">
          <option value="">Any status</option><option value="overdue">Overdue</option><option value="due_soon">Due soon</option><option value="ok">Up to date</option>
        </select>
        <select value={lifecycle} onChange={(e) => set("lifecycle", e.target.value)} style={{ width: "auto" }} aria-label="Lifecycle">
          <option value="">Active and stored</option>{Object.entries(list.data?.lifecycle || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      <div className="list card tight">
        {list.error && !list.data ? <ErrorBox error={list.error} retry={list.reload} /> : !list.data ? <Spinner /> : rows.length ? rows
          .filter((r) => lifecycle || !["retired", "disposed"].includes(r.lifecycle_status))
          .map((r) => (
            <Link key={r.id} to={`/equipment/${r.id}`}>
              <Thumb src={r.photo_url} icon={<Tractor size={24} />} />
              <div className="grow">
                <div className="t">{r.name}{r.label ? ` · ${r.label}` : ""} {r.attention !== "ok" && <AttnBadge a={r.attention} n={r.attention === "overdue" ? r.overdue : r.due_soon} />}</div>
                <div className="s">{[r.manufacturer, r.model_number, r.equipment_type].filter(Boolean).join(" · ")}{r.lifecycle_status !== "active" ? ` · ${r.lifecycle_label}` : ""}</div>
                <div className="s">Next: {nextText(r.next)}{r.hours !== null ? ` · ${r.hours} h` : ""}{r.last_service ? ` · last service ${fmtDate(r.last_service.on)}` : ""}</div>
              </div>
            </Link>
          )) : <Empty>{q || attention || type ? "No equipment matches." : "No equipment yet. Open an item (a mower, a generator) and choose Manage as equipment."}</Empty>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- one piece of equipment

export function EquipmentPage() {
  const { id } = useParams();
  const itemId = Number(id);
  const me = useMe();
  const edit = me.can_edit;
  const [key, setKey] = useState(0);
  const refresh = () => setKey((k) => k + 1);
  const ov = useApi(() => api.get<Overview>(`equipment/${itemId}`), [itemId, key]);
  const cl = useApi(() => api.get<Checklist>(`equipment/${itemId}/checklist`), [itemId, key]);
  const summary = useApi(() => api.get<MaintenanceSummary>(`items/${itemId}/maintenance`), [itemId, key]);
  const [sheet, setSheet] = useState<null | { kind: "done" | "edit" | "add" | "parts" | "manual"; task?: MaintTask & { parts?: ReadinessLine[] } }>(null);
  const { busy, run } = useAction();
  const close = () => setSheet(null);
  const done = () => { setSheet(null); refresh(); };

  if (ov.error && !ov.data) {
    return <div className="page"><ErrorBox error={ov.error} retry={ov.reload} />
      {/not managed/.test(ov.error) && <p><Link to={`/items/${itemId}`}>Open it as an item</Link></p>}</div>;
  }
  if (!ov.data) return <Spinner />;
  const o = ov.data;
  const it = o.item;
  const p = o.profile;
  const ident = [it.manufacturer, it.model_number && `model ${it.model_number}`, it.serial_number && `serial ${it.serial_number}`].filter(Boolean).join(" · ");
  const engine = [p.engine_manufacturer, p.engine_model].filter(Boolean).join(" ");
  const toBuy = o.parts.filter((l) => l.status === "missing" || l.status === "partial");

  const setNA = async (t: MaintTask, value: boolean) => {
    if (await run(() => api.post(`maintenance-tasks/${t.id}/not-applicable`, { value }), value ? "Disabled for this equipment" : "Back in the schedule")) refresh();
  };
  const approve = async (t: MaintTask) => {
    if (await run(() => api.patch(`maintenance-tasks/${t.id}`, { review_status: "approved" }), "Kept your version")) refresh();
  };
  const useManual = async (t: MaintTask) => {
    const m = t.manual_values || {};
    if (await run(() => api.patch(`maintenance-tasks/${t.id}`, { interval_hours: m.interval_hours ?? null, interval_days: m.interval_days ?? null,
      interval_cycles: m.interval_cycles ?? null, review_status: "approved" }), "Now follows the manual")) refresh();
  };
  const addToList = async () => {
    for (const l of toBuy) if (l.buy) await api.post("shopping-list", { product_id: l.buy.product_id, quantity: l.buy.quantity });
    await run(async () => undefined, `${toBuy.length} added to the shopping list`);
  };

  return (
    <div className="page">
      <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
        <div style={{ width: 84, flex: "none" }}><Thumb src={it.photo?.thumb_url} size="lg" icon={<Tractor size={32} />} /></div>
        <div className="grow">
          <h1 style={{ margin: "0 0 2px" }}>{it.display_name}</h1>
          {ident && <div className="muted">{ident}</div>}
          <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
            <AttnBadge a={o.summary.attention} n={o.summary.attention === "overdue" ? o.summary.overdue : o.summary.attention === "due_soon" ? o.summary.due_soon : undefined} />
            <span className="badge">{o.lifecycle_label}</span>
            {p.equipment_type && <span className="badge">{p.equipment_type}</span>}
            {o.meters.hours !== null && <span className="badge"><Gauge size={12} /> {o.meters.hours} h</span>}
          </div>
        </div>
      </div>

      <div className="row wrap" style={{ gap: 6, margin: "12px 0" }}>
        <a className="btn small" href={url(`api/v1/equipment/${itemId}/checklist.pdf`)} target="_blank" rel="noreferrer"><Printer size={16} /> Print checklist</a>
        <a className="btn small ghost" href={url(`api/v1/items/${itemId}/service-history.pdf`)} download>Service history PDF</a>
        {o.plan.manuals.map((m) => <a key={m.id} className="btn small ghost" href={url(m.url)} target="_blank" rel="noreferrer"><BookOpen size={16} /> {m.name || "Manual"}</a>)}
        <Link className="btn small ghost" to={`/items/${itemId}`}><Package size={16} /> Item record</Link>
      </div>

      {/* What needs attention */}
      <Section title="Status">
        <div className="grid two">
          <div><b>Next</b><div>{nextText(o.summary.next)}</div></div>
          <div><b>Last completed</b><div>{o.last_completed ? `${o.last_completed.title}, ${fmtDate(o.last_completed.performed_on)}${o.last_completed.hours !== null ? ` at ${o.last_completed.hours} h` : ""}` : "Nothing recorded yet"}</div></div>
          <div><b>Where it is</b><div>{it.placement.path}</div></div>
          <div><b>Plan</b><div style={{ color: o.plan.state === "generic" || o.plan.state === "no_manual" ? "var(--warn)" : undefined }}>
            {o.plan.state.startsWith("from_manual") && <ShieldCheck size={14} style={{ verticalAlign: "-2px" }} />} {PLAN_TEXT[o.plan.state]}
            {o.plan.needs_review > 0 && <div className="s" style={{ color: "var(--warn)" }}>{o.plan.needs_review} task{o.plan.needs_review === 1 ? "" : "s"} to review below</div>}
          </div></div>
        </div>
        {o.upcoming.length > 0 && (
          <ul className="list" style={{ marginTop: 10 }}>
            {o.upcoming.map((t) => (
              <li key={t.id}><div className="grow"><div className="t">{t.title} <TaskBadge t={t} /></div><div className="s">{dueText(t)}</div></div>
                {edit && <button className="btn small primary" onClick={() => setSheet({ kind: "done", task: t })}><Check size={16} /> Done</button>}</li>
            ))}
          </ul>
        )}
        {edit && (o.plan.state === "no_manual" || o.plan.state === "manual_not_read" || o.plan.state === "generic") && me.app.ai.enabled && (
          <button className="btn small" style={{ marginTop: 10 }} onClick={() => setSheet({ kind: "manual" })}><BookOpen size={16} /> Read the manufacturer's manual</button>
        )}
      </Section>

      {/* The checklist */}
      <Section title="Maintenance checklist" action={edit && <button className="btn small" onClick={() => setSheet({ kind: "add" })}>Add task</button>}>
        {cl.error && !cl.data ? <ErrorBox error={cl.error} retry={cl.reload} /> : !cl.data ? <Spinner /> : !cl.data.groups.length ? (
          <Empty>No tasks yet.{me.app.ai.enabled ? " Read the manual to build the checklist from it, or add tasks." : " Add tasks, e.g. from the manual's maintenance table."}</Empty>
        ) : cl.data.groups.map((g) => (
          <div key={g.key} style={{ marginBottom: 12 }}>
            <h3 style={{ margin: "8px 0 4px" }}>{g.label}</h3>
            <ul className="list">
              {g.tasks.map((t) => (
                <li key={t.id} style={t.not_applicable ? { opacity: 0.55 } : undefined}>
                  <div className="grow" onClick={() => edit && setSheet({ kind: "edit", task: t })} style={{ cursor: edit ? "pointer" : undefined }}>
                    <div className="t">{t.title} {t.not_applicable ? <span className="badge">disabled</span> : t.status !== "manual" && <TaskBadge t={t} />}</div>
                    <div className="s">{t.not_applicable ? "Not part of this equipment's schedule" : t.status === "manual" && EVERY[g.key] ? EVERY[g.key] : dueText(t)}{t.last_done_on ? ` · last ${fmtDate(t.last_done_on)}${t.last_done_hours !== null ? ` at ${t.last_done_hours} h` : ""}` : ""}</div>
                    {t.instructions && <div className="s" style={{ whiteSpace: "pre-line" }}>{t.instructions}</div>}
                    <div className="s">
                      {t.source === "manual" ? <><ShieldCheck size={12} style={{ verticalAlign: "-2px" }} /> {t.source_ref || "Manufacturer's manual"}</>
                        : t.source === "generic" ? <span style={{ color: "var(--warn)" }}>Generic: {t.source_ref || "typical schedule, not this model's manual"}</span>
                        : "Added by hand"}
                      {t.user_edited && t.source === "manual" ? " · changed by you" : ""}
                    </div>
                    {t.parts.length > 0 && <div className="s" onClick={(e) => { e.stopPropagation(); if (edit) setSheet({ kind: "parts", task: t }); }}>
                      Needs: {t.parts.map((l) => `${l.product.name} ×${l.quantity}${l.unit ? ` ${l.unit}` : ""}`).join(", ")}</div>}
                    {t.review_status === "needs_review" && t.manual_values && (
                      <div className="notice" style={{ marginTop: 6 }} onClick={(e) => e.stopPropagation()}>
                        The manual now says {[t.manual_values.interval_hours && `every ${t.manual_values.interval_hours} h`, t.manual_values.interval_days && `every ${t.manual_values.interval_days} days`].filter(Boolean).join(" or ") || "something different"}; you have {[t.interval_hours && `every ${t.interval_hours} h`, t.interval_days && `every ${t.interval_days} days`].filter(Boolean).join(" or ") || "no interval"}.
                        {edit && <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
                          <button className="btn small" disabled={busy} onClick={() => approve(t)}>Keep mine</button>
                          <button className="btn small" disabled={busy} onClick={() => useManual(t)}>Use the manual's</button></div>}
                      </div>
                    )}
                  </div>
                  {edit && (
                    <div className="stack" style={{ gap: 4 }}>
                      {!t.not_applicable && <button className="btn small primary" onClick={() => setSheet({ kind: "done", task: t })} aria-label={`Mark ${t.title} done`}><Check size={16} /> Done</button>}
                      <button className="btn small ghost" disabled={busy} onClick={() => setNA(t, !t.not_applicable)}>{t.not_applicable ? "Enable" : "Disable"}</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Section>

      {/* Parts and consumables */}
      <Section title="Parts and consumables" action={edit && toBuy.some((l) => l.buy) && <button className="btn small" onClick={addToList}><ShoppingCart size={16} /> Add {toBuy.length} to shopping list</button>}>
        {!o.parts.length ? <Empty>No parts linked to its tasks yet. Reading the manual links them, or tap a task's parts.</Empty> : (
          <ul className="list">
            {o.parts.map((l) => {
              const where = l.sources.flatMap((s) => s.items.filter((i) => i.where).map((i) => `${i.quantity > 1 ? `${i.quantity} in ` : ""}${i.where}`));
              return (
                <li key={l.key}>
                  <div className="grow">
                    <div className="t">{l.product.name} <ReadyBadge status={l.status} />{l.needed_soon && <span className="badge">needed soon</span>}</div>
                    <div className="s">{l.quantity}{l.unit ? ` ${l.unit}` : ""} per service · for {l.tasks.map((t) => t.title).join(", ")}</div>
                    {where.length > 0 && <div className="s">Kept in: {where.slice(0, 3).join("; ")}</div>}
                    {l.buy && <div className="s" style={{ color: "var(--warn)" }}>Buy: {l.buy.quantity} × {l.buy.name}</div>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      {/* Lifecycle, purchase, warranty, costs */}
      <Section title="Details">
        <div className="grid two">
          {it.purchase_date && <div><b>Bought</b><div>{fmtDate(it.purchase_date)}{it.purchase_price ? `, ${money(it.purchase_price, it.currency || me.app.currency)}` : ""}{p.acquired_from ? ` from ${p.acquired_from}` : it.retailer ? ` from ${it.retailer}` : ""}</div></div>}
          {o.warranty && <div><b>Warranty</b><div style={{ color: o.warranty.active ? undefined : "var(--muted)" }}>{o.warranty.active ? `Until ${fmtDate(o.warranty.expires)} (${o.warranty.days_left} days)` : `Ended ${fmtDate(o.warranty.expires)}`}</div></div>}
          {engine && <div><b>Engine</b><div>{engine}{p.engine_serial ? ` · ${p.engine_serial}` : ""}</div></div>}
          {p.fuel_type && <div><b>Fuel</b><div>{p.fuel_type}</div></div>}
          <div><b>Maintenance spent</b><div>{money(o.costs.total, me.app.currency) || "—"} over {o.costs.services} service{o.costs.services === 1 ? "" : "s"}</div></div>
          {o.entities.length > 0 && <div><b>Home Assistant</b><div className="mono s">{o.entities.join(", ")}</div></div>}
        </div>
        {p.specs.length > 0 && <div className="s" style={{ marginTop: 8 }}>{p.specs.filter((s) => s.value).map((s) => `${s.label}: ${s.value}`).join(" · ")}</div>}
      </Section>

      {/* Meter, readings, sensors, service history, editing: the existing maintenance tools */}
      <EquipmentSection itemId={itemId} edit={edit} hideTasks reloadKey={key} onChanged={refresh} />

      {sheet?.kind === "done" && sheet.task && summary.data && (
        <ServiceSheet itemId={itemId} task={sheet.task} summary={summary.data} needs={sheet.task.parts} onClose={close} onDone={done} />
      )}
      {(sheet?.kind === "edit" || sheet?.kind === "add") && <TaskSheet itemId={itemId} task={sheet.kind === "edit" ? sheet.task : undefined} onClose={close} onDone={done} />}
      {sheet?.kind === "parts" && sheet.task && <TaskPartsSheet taskId={sheet.task.id} title={sheet.task.title} rows={sheet.task.parts || []} onClose={close} onDone={done} />}
      {sheet?.kind === "manual" && <ManualSheet itemId={itemId} onClose={close} onDone={done} />}
    </div>
  );
}

// ---------------------------------------------------------------- reports

type ReportKind = "overdue" | "upcoming" | "history" | "consolidated";

export function MaintenanceReportsPage() {
  const [params, setParams] = useSearchParams();
  const kind = (params.get("kind") as ReportKind) || "overdue";
  const days = Number(params.get("days") || 30);
  const me = useMe();
  const rep = useApi(() => api.get<{ tasks?: MaintTask[]; events?: (ServiceEntry & { item: { id: number; name: string } })[]; total_cost?: number; equipment?: EquipmentRow[] }>(
    `maintenance/reports/${kind}`, kind === "upcoming" ? { days } : undefined), [kind, days]);
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); p.set(k, v); setParams(p, { replace: true }); };
  const titles: Record<ReportKind, string> = { overdue: "Overdue", upcoming: "Upcoming", history: "History", consolidated: "All equipment" };
  return (
    <div className="page">
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}>Maintenance reports</h1>
        <button className="btn small" onClick={() => window.print()}><Printer size={16} /> Print</button>
      </div>
      <div className="tabs" role="tablist" style={{ margin: "12px 0" }}>
        {(Object.keys(titles) as ReportKind[]).map((k) => <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""} onClick={() => set("kind", k)}>{titles[k]}</button>)}
      </div>
      {kind === "upcoming" && (
        <label className="field" style={{ maxWidth: 220 }}>Due within
          <select value={days} onChange={(e) => set("days", e.target.value)}><option value="7">a week</option><option value="30">a month</option><option value="90">3 months</option><option value="365">a year</option></select>
        </label>
      )}
      <div className="card tight">
        {rep.error && !rep.data ? <ErrorBox error={rep.error} retry={rep.reload} /> : !rep.data || rep.loading ? <Spinner /> : kind === "history" ? (
          rep.data.events?.length ? (
            <>
              <ul className="list">{rep.data.events.map((e) => (
                <li key={e.id}><div className="grow">
                  <div className="t"><Link to={`/equipment/${e.item.id}`}>{e.item.name}</Link>: {e.title}</div>
                  <div className="s">{fmtDate(e.performed_on)}{e.hours !== null ? ` · ${e.hours} h` : ""}{e.performed_by ? ` · ${e.performed_by}` : ""}
                    {(e.labor_cost || e.parts_cost) ? ` · ${money((e.labor_cost || 0) + (e.parts_cost || 0), e.currency || me.app.currency)}` : ""}</div>
                  {e.parts.length > 0 && <div className="s">Parts: {e.parts.map((p) => `${p.description}${p.quantity !== 1 ? ` ×${p.quantity}` : ""}`).join(", ")}</div>}
                </div></li>))}</ul>
              <div style={{ padding: 12 }}><b>Total: {money(rep.data.total_cost || 0, me.app.currency)}</b></div>
            </>
          ) : <Empty>No maintenance recorded in the last year.</Empty>
        ) : kind === "consolidated" ? (
          rep.data.equipment?.length ? <ul className="list">{rep.data.equipment.map((r) => (
            <li key={r.id}><Link to={`/equipment/${r.id}`} className="grow" style={{ display: "block" }}>
              <div className="t">{r.name} {r.attention !== "ok" && <AttnBadge a={r.attention} n={r.attention === "overdue" ? r.overdue : r.due_soon} />}</div>
              <div className="s">{r.tasks} task{r.tasks === 1 ? "" : "s"} · next: {nextText(r.next)} · {PLAN_TEXT[r.plan]}</div>
              <div className="s">{r.last_service ? `Last service ${fmtDate(r.last_service.on)} (${r.last_service.title})` : "No service recorded"}{r.hours !== null ? ` · ${r.hours} h` : ""}</div>
            </Link></li>))}</ul> : <Empty>No equipment yet.</Empty>
        ) : rep.data.tasks?.length ? (
          <ul className="list">{rep.data.tasks.map((t) => (
            <li key={t.id}><div className="grow">
              <div className="t"><Link to={`/equipment/${t.item?.id}`}>{t.item?.name}</Link>: {t.title} <TaskBadge t={t} /></div>
              <div className="s">{dueText(t)}{t.last_done_on ? ` · last ${fmtDate(t.last_done_on)}` : ""}</div>
            </div></li>))}</ul>
        ) : <Empty>{kind === "overdue" ? "Nothing is overdue." : "Nothing due in that time."}</Empty>}
      </div>
      <p className="muted s"><FileText size={12} /> Each piece of equipment's own printable checklist is on its page (Print checklist).</p>
    </div>
  );
}
