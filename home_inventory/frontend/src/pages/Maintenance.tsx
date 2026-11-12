import { Fragment, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Check, Clock, Gauge, Plus, Trash2, Wrench } from "lucide-react";
import {
  api,
  uploadPhoto,
  url,
  type EquipmentProfile,
  type MaintDashboard,
  type MaintenanceSummary,
  type MaintPreset,
  type MaintTask,
  type ServiceEntry,
  type SpecRow,
} from "../api";
import { ErrorBox, Section, Sheet, Spinner, fmtDate, money, useAction, useApi, useMe } from "../components/ui";

const today = () => new Date().toISOString().slice(0, 10);
const num = (v: string) => (v.trim() === "" ? null : Number(v));

// ---------------------------------------------------------------- shared bits

export function TaskBadge({ t }: { t: Pick<MaintTask, "status"> }) {
  const map: Record<string, [string, string]> = {
    overdue: ["danger", "Overdue"],
    due_soon: ["warn", "Due soon"],
    deferred: ["blue", "Deferred"],
    ok: ["ok", "OK"],
    manual: ["", "As needed"],
    not_applicable: ["", "Not applicable"],
  };
  const [cls, label] = map[t.status];
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function dueText(t: MaintTask): string {
  const parts: string[] = [];
  if (t.days_left !== null) parts.push(t.days_left < 0 ? `${-t.days_left} d overdue` : t.days_left === 0 ? "today" : `in ${t.days_left} d`);
  if (t.hours_left !== null) parts.push(t.hours_left <= 0 ? `${Math.abs(t.hours_left).toFixed(1)} h over` : `in ${t.hours_left.toFixed(1)} h`);
  if (t.cycles_left !== null) parts.push(t.cycles_left <= 0 ? `${Math.abs(t.cycles_left)} uses over` : `in ${t.cycles_left} uses`);
  if (t.status === "deferred" && t.deferred_until) parts.unshift(`deferred to ${fmtDate(t.deferred_until)}`);
  return parts.join(" · ") || "when needed";
}

function interval(t: MaintTask): string {
  const p: string[] = [];
  if (t.interval_hours) p.push(`${t.interval_hours} h`);
  if (t.interval_cycles) p.push(`${t.interval_cycles} uses`);
  if (t.interval_days) p.push(t.interval_days % 365 === 0 ? `${t.interval_days / 365} yr` : t.interval_days % 30 === 0 ? `${t.interval_days / 30} mo` : `${t.interval_days} d`);
  return p.length ? `every ${p.join(" or ")}` : "one-off / as needed";
}

function ServiceRow({ e, currency, showItem }: { e: ServiceEntry; currency: string; showItem?: boolean }) {
  const cost = (e.labor_cost || 0) + (e.parts_cost || 0);
  const outcome = e.outcome === "completed" ? null : <span className="badge">{e.outcome.replace("_", " ")}</span>;
  return (
    <li>
      <Wrench size={18} />
      <div className="grow">
        <div className="t">
          {showItem && e.item?.id ? <Link to={`/items/${e.item.id}`}>{e.item.name}</Link> : null}
          {showItem ? " – " : ""}
          {e.title} {outcome}
        </div>
        <div className="s">
          {fmtDate(e.performed_on)}
          {e.hours !== null ? ` · ${e.hours} h` : ""}
          {e.cycles !== null ? ` · ${e.cycles} uses` : ""}
          {e.performed_by ? ` · ${e.performed_by}` : ""}
          {cost ? ` · ${money(cost, e.currency || currency)}` : ""}
        </div>
        {e.work_performed && <div className="s" style={{ whiteSpace: "pre-line" }}>{e.work_performed}</div>}
        {e.parts.length > 0 && <div className="s">Parts: {e.parts.map((p) => `${p.quantity !== 1 ? `${p.quantity} × ` : ""}${p.description}${p.part_number ? ` (${p.part_number})` : ""}`).join(", ")}</div>}
        {e.attachments.length > 0 && (
          <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
            {e.attachments.map((a) => (
              <a key={a.id} href={url(a.url)} target="_blank" rel="noreferrer">
                {a.thumb_url ? <img src={url(a.thumb_url)} alt="" width={48} height={48} style={{ objectFit: "cover", borderRadius: 8 }} /> : "document"}
              </a>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- item page section

export function EquipmentSection({ itemId, edit, onChanged, initial }: { itemId: number; edit: boolean; onChanged?: () => void; initial?: "meter" | "service" }) {
  const me = useMe();
  const s = useApi(() => api.get<MaintenanceSummary>(`items/${itemId}/maintenance`), [itemId]);
  const [sheet, setSheet] = useState<null | { kind: "setup" | "profile" | "meter" | "service" | "task"; task?: MaintTask }>(edit && initial ? { kind: initial } : null);
  const close = () => setSheet(null);
  const done = (data?: MaintenanceSummary) => {
    if (data) s.setData(data);
    else s.reload();
    setSheet(null);
    onChanged?.();
  };
  if (s.loading && !s.data) return null;
  if (s.error || !s.data) return <ErrorBox error={s.error} />;
  const d = s.data;

  if (!d.profile && !d.tasks.length && !d.history.length) {
    if (!edit) return null;
    return (
      <Section title="Maintenance">
        <p className="muted" style={{ marginTop: 0 }}>Track service schedules, operating hours and repairs for equipment such as mowers, generators and saws.</p>
        <button className="btn" onClick={() => setSheet({ kind: "setup" })}><Wrench size={18} /> Track maintenance</button>
        {sheet?.kind === "setup" && <SetupSheet itemId={itemId} onClose={close} onDone={done} />}
      </Section>
    );
  }

  const meter = d.profile?.meter ?? "hours";
  return (
    <Section
      title={d.profile?.equipment_type ? `Maintenance · ${d.profile.equipment_type}` : "Maintenance"}
      action={edit && <button className="btn small" onClick={() => setSheet({ kind: "profile" })}>Edit</button>}
    >
      {(meter === "hours" || meter === "both" || meter === "cycles") && (
        <div className="row between wrap" style={{ marginBottom: 10 }}>
          <div className="row" style={{ gap: 8 }}>
            <Gauge size={20} />
            <b style={{ fontSize: "1.2rem" }}>
              {meter !== "cycles" && (d.meters.hours !== null ? `${d.meters.hours} h` : "No hours yet")}
              {meter === "both" && " · "}
              {(meter === "cycles" || meter === "both") && (d.meters.cycles !== null ? `${d.meters.cycles} uses` : "No uses logged")}
            </b>
          </div>
          {edit && <button className="btn small" onClick={() => setSheet({ kind: "meter" })}><Clock size={16} /> Log {meter === "cycles" ? "uses" : "hours"}</button>}
        </div>
      )}

      {d.tasks.length > 0 && (
        <ul className="list">
          {d.tasks.map((t) => (
            <li key={t.id}>
              <div className="grow" onClick={() => edit && setSheet({ kind: "task", task: t })} style={{ cursor: edit ? "pointer" : undefined }}>
                <div className="t">{t.title} <TaskBadge t={t} /></div>
                <div className="s">{dueText(t)} · {interval(t)}{t.last_done_on ? ` · last ${fmtDate(t.last_done_on)}` : ""}</div>
              </div>
              {edit && t.status !== "not_applicable" && (
                <button className="btn small primary" onClick={() => setSheet({ kind: "service", task: t })} aria-label={`Record ${t.title}`}><Check size={16} /> Done</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {edit && (
        <div className="row wrap" style={{ marginTop: 10 }}>
          <button className="btn" onClick={() => setSheet({ kind: "service" })}><Wrench size={18} /> Log service</button>
          <button className="btn" onClick={() => setSheet({ kind: "task" })}><Plus size={18} /> Add task</button>
        </div>
      )}

      {d.profile && <ProfileView p={d.profile} />}

      <h3 style={{ margin: "16px 0 6px" }}>Service history</h3>
      {d.history.length ? (
        <ul className="list">{d.history.map((e) => <ServiceRow key={e.id} e={e} currency={me.app.currency} />)}</ul>
      ) : (
        <p className="muted">No service recorded yet. Use <b>Log service</b> to add past work too.</p>
      )}

      {sheet?.kind === "setup" && <SetupSheet itemId={itemId} onClose={close} onDone={done} />}
      {sheet?.kind === "profile" && d.profile && <ProfileSheet itemId={itemId} profile={d.profile} onClose={close} onDone={done} />}
      {sheet?.kind === "meter" && <MeterSheet itemId={itemId} summary={d} onClose={close} onDone={done} />}
      {sheet?.kind === "service" && <ServiceSheet itemId={itemId} task={sheet.task} summary={d} onClose={close} onDone={done} />}
      {sheet?.kind === "task" && <TaskSheet itemId={itemId} task={sheet.task} onClose={close} onDone={done} />}
    </Section>
  );
}

function ProfileView({ p }: { p: EquipmentProfile }) {
  const rows: [string, ReactNode][] = [];
  if (p.engine_manufacturer || p.engine_model) rows.push(["Engine", [p.engine_manufacturer, p.engine_model].filter(Boolean).join(" ")]);
  if (p.engine_serial) rows.push(["Engine serial", <span className="mono">{p.engine_serial}</span>]);
  if (p.fuel_type) rows.push(["Fuel", p.fuel_type]);
  for (const sp of p.specs) {
    if (!sp.value && !sp.part_number) continue;
    rows.push([sp.label, <>{sp.value}{sp.part_number ? <span className="mono"> {sp.part_number}</span> : null}</>]);
  }
  if (p.notes) rows.push(["Notes", <span style={{ whiteSpace: "pre-line" }}>{p.notes}</span>]);
  if (!rows.length) return null;
  return (
    <dl className="kv" style={{ marginTop: 14 }}>
      {rows.map(([k, v], i) => (<Fragment key={i}><dt>{k}</dt><dd>{v}</dd></Fragment>))}
    </dl>
  );
}

// ---------------------------------------------------------------- sheets

function SetupSheet({ itemId, onClose, onDone }: { itemId: number; onClose: () => void; onDone: (d: MaintenanceSummary) => void }) {
  const presets = useApi(() => api.get<MaintPreset[]>("maintenance/presets"), []);
  const { busy, run } = useAction();
  const [current, setCurrent] = useState("");
  const pick = async (key: string) => {
    const meter = presets.data?.find((p) => p.key === key)?.meter;
    const body: Record<string, unknown> = { preset: key };
    if (current !== "" && meter && meter !== "none") body[meter === "cycles" ? "current_cycles" : "current_hours"] = Number(current);
    const d = await run(() => api.put<MaintenanceSummary>(`items/${itemId}/equipment`, body), "Maintenance tracking started");
    if (d) onDone(d);
  };
  return (
    <Sheet title="What kind of equipment?" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>This adds typical service tasks as a starting point. Check them against your manual. You can change or remove any of them.</p>
      <label className="field" style={{ marginBottom: 12 }}>
        Current hours or uses (optional)
        <input type="number" inputMode="decimal" step="0.1" min={0} value={current} onChange={(e) => setCurrent(e.target.value)} placeholder="e.g. 212 for a used machine" />
        <span className="hint">Schedules start from here, so a used machine isn't shown as overdue for everything.</span>
      </label>
      {presets.loading ? <Spinner /> : (
        <div className="quick">
          {presets.data?.map((p) => (
            <button key={p.key} disabled={busy} onClick={() => pick(p.key)}>
              <span className="ico"><Wrench size={20} /></span>
              {p.name}
              <span className="muted" style={{ fontSize: ".75rem" }}>{p.tasks.length ? `${p.tasks.length} tasks` : "no tasks"}</span>
            </button>
          ))}
        </div>
      )}
    </Sheet>
  );
}

function ProfileSheet({ itemId, profile, onClose, onDone }: { itemId: number; profile: EquipmentProfile; onClose: () => void; onDone: (d?: MaintenanceSummary) => void }) {
  const [p, setP] = useState<EquipmentProfile>({ ...profile, specs: profile.specs.map((s) => ({ ...s })) });
  const { busy, run } = useAction();
  const set = (k: keyof EquipmentProfile) => (e: { target: { value: string } }) => setP({ ...p, [k]: e.target.value });
  const setSpec = (i: number, k: keyof SpecRow, v: string) => setP({ ...p, specs: p.specs.map((s, j) => (j === i ? { ...s, [k]: v } : s)) });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const specs = p.specs.filter((s) => s.label.trim());
    const d = await run(() => api.put<MaintenanceSummary>(`items/${itemId}/equipment`, { ...p, specs }), "Saved");
    if (d) onDone(d);
  };
  const stop = async () => {
    if (!window.confirm("Stop tracking maintenance for this item? Its tasks are removed; service history is kept.")) return;
    await run(() => api.del(`items/${itemId}/equipment`), "Maintenance tracking stopped");
    onDone();
  };
  return (
    <Sheet title="Equipment details" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="grid two">
          <label className="field">Equipment type<input value={p.equipment_type || ""} onChange={set("equipment_type")} placeholder="Lawn mower" /></label>
          <label className="field">Usage meter
            <select value={p.meter} onChange={set("meter")}>
              <option value="hours">Operating hours</option>
              <option value="cycles">Uses / tanks</option>
              <option value="both">Hours and uses</option>
              <option value="none">None (calendar only)</option>
            </select>
          </label>
          <label className="field">Engine make<input value={p.engine_manufacturer || ""} onChange={set("engine_manufacturer")} placeholder="Honda, Briggs & Stratton…" /></label>
          <label className="field">Engine model<input value={p.engine_model || ""} onChange={set("engine_model")} /></label>
          <label className="field">Engine serial<input value={p.engine_serial || ""} onChange={set("engine_serial")} /></label>
          <label className="field">Fuel<input value={p.fuel_type || ""} onChange={set("fuel_type")} placeholder="Gasoline, 50:1 mix, battery…" /></label>
        </div>
        <b>Specifications and parts</b>
        {p.specs.map((s, i) => (
          <div key={i} className="row" style={{ gap: 6, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 1.2 }}>{i === 0 ? "Name" : ""}<input value={s.label} onChange={(e) => setSpec(i, "label", e.target.value)} /></label>
            <label className="field" style={{ flex: 1.2 }}>{i === 0 ? "Value" : ""}<input value={s.value || ""} onChange={(e) => setSpec(i, "value", e.target.value)} placeholder={s.kind === "part" ? "Brand / type" : "e.g. SAE 30, 0.6 qt"} /></label>
            <label className="field" style={{ flex: 1 }}>{i === 0 ? "Part no." : ""}<input value={s.part_number || ""} onChange={(e) => setSpec(i, "part_number", e.target.value)} /></label>
            <button type="button" className="iconbtn" aria-label="Remove" onClick={() => setP({ ...p, specs: p.specs.filter((_, j) => j !== i) })}><Trash2 size={18} /></button>
          </div>
        ))}
        <button type="button" className="btn small" onClick={() => setP({ ...p, specs: [...p.specs, { label: "", value: "", kind: "part", part_number: "" }] })}><Plus size={16} /> Add row</button>
        <label className="field">Notes<textarea rows={3} value={p.notes || ""} onChange={set("notes")} /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
        <button type="button" className="btn block" disabled={busy} onClick={stop}>Stop tracking maintenance</button>
      </form>
    </Sheet>
  );
}

function MeterSheet({ itemId, summary, onClose, onDone }: { itemId: number; summary: MaintenanceSummary; onClose: () => void; onDone: (d: MaintenanceSummary) => void }) {
  const cyclesOnly = summary.profile?.meter === "cycles";
  const [kind, setKind] = useState<"hours" | "cycles">(cyclesOnly ? "cycles" : "hours");
  const current = kind === "hours" ? summary.meters.hours : summary.meters.cycles;
  const [value, setValue] = useState(current !== null ? String(current) : "");
  const [estimated, setEstimated] = useState(false);
  const [correction, setCorrection] = useState(false);
  const [note, setNote] = useState("");
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const d = await run(() => api.post<MaintenanceSummary>(`items/${itemId}/meter-readings`, { kind, value: Number(value), estimated, correction, note: note || null }), "Reading saved");
    if (d) onDone(d);
  };
  return (
    <Sheet title={kind === "hours" ? "Operating hours" : "Uses"} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        {summary.profile?.meter === "both" && (
          <div className="tabs">
            <button type="button" className={kind === "hours" ? "active" : ""} onClick={() => setKind("hours")}>Hours</button>
            <button type="button" className={kind === "cycles" ? "active" : ""} onClick={() => setKind("cycles")}>Uses</button>
          </div>
        )}
        <label className="field">
          {kind === "hours" ? "Meter reading (hours)" : "Total uses / tanks"}
          <input type="number" inputMode="decimal" step={kind === "hours" ? "0.1" : "1"} min={0} required autoFocus value={value} onChange={(e) => setValue(e.target.value)} style={{ fontSize: "1.4rem" }} />
          {current !== null && <span className="hint">Last reading: {current}</span>}
        </label>
        <label className="row"><input type="checkbox" checked={estimated} onChange={(e) => setEstimated(e.target.checked)} /> Estimate (no hour meter)</label>
        <label className="row"><input type="checkbox" checked={correction} onChange={(e) => setCorrection(e.target.checked)} /> Correction (meter replaced or mis-read)</label>
        <label className="field">Note<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button className="btn primary block" disabled={busy || value === ""}>Save reading</button>
      </form>
    </Sheet>
  );
}

interface PartForm { description: string; part_number: string; quantity: string; unit_cost: string }

export function ServiceSheet({ itemId, task, summary, onClose, onDone, postTo, title }: {
  itemId: number;
  task?: MaintTask;
  summary: MaintenanceSummary;
  onClose: () => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onDone: (d?: any) => void;
  /** Post somewhere else (e.g. a repair job) instead of the item's history. */
  postTo?: string;
  title?: string;
}) {
  const me = useMe();
  const meter = summary.profile?.meter ?? "hours";
  const [f, setF] = useState({
    title: task?.title || "",
    performed_on: today(),
    hours: meter !== "cycles" && summary.meters.hours !== null ? String(summary.meters.hours) : "",
    cycles: (meter === "cycles" || meter === "both") && summary.meters.cycles !== null ? String(summary.meters.cycles) : "",
    work_performed: "",
    notes: "",
    performed_by: me.name,
    labor_cost: "",
  });
  const [parts, setParts] = useState<PartForm[]>([]);
  const [photos, setPhotos] = useState<{ id: number; thumb?: string }[]>([]);
  const [more, setMore] = useState<null | "defer">(null);
  const [deferTo, setDeferTo] = useState("");
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const payload = () => ({
    title: f.title || null,
    performed_on: f.performed_on,
    hours: num(f.hours),
    cycles: f.cycles === "" ? null : Math.round(Number(f.cycles)),
    work_performed: f.work_performed || null,
    notes: f.notes || null,
    performed_by: f.performed_by || null,
    labor_cost: f.labor_cost === "" ? null : f.labor_cost,
    parts: parts.filter((p) => p.description.trim()).map((p) => ({
      description: p.description, part_number: p.part_number || null, quantity: p.quantity || "1", unit_cost: p.unit_cost === "" ? null : p.unit_cost,
    })),
    attachment_ids: photos.map((p) => p.id),
  });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const d = await run(
      () => (postTo ? api.post(postTo, payload()) : task ? api.post<MaintenanceSummary>(`maintenance-tasks/${task.id}/complete`, payload()) : api.post<MaintenanceSummary>(`items/${itemId}/service-events`, payload())),
      "Service recorded",
    );
    if (d) onDone(d);
  };
  const action = async (path: string, body: unknown, msg: string) => {
    const d = await run(() => api.post<MaintenanceSummary>(`maintenance-tasks/${task!.id}/${path}`, body), msg);
    if (d) onDone(d);
  };
  const addPhoto = async (files: FileList | null) => {
    for (const file of Array.from(files || [])) {
      const a = await run(() => uploadPhoto(file));
      if (a) setPhotos((p) => [...p, { id: a.id, thumb: a.thumb_url || undefined }]);
    }
  };
  return (
    <Sheet title={title || (task ? task.title : "Log service")} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        {task?.instructions && <div className="notice" style={{ whiteSpace: "pre-line" }}>{task.instructions}</div>}
        {!task && <label className="field">What was done<input required value={f.title} onChange={set("title")} placeholder="Oil change, carburetor cleaned…" /></label>}
        <div className="grid two">
          <label className="field">Date<input type="date" max={today()} required value={f.performed_on} onChange={set("performed_on")} /><span className="hint">Past dates are fine for older history.</span></label>
          {meter !== "cycles" && meter !== "none" && <label className="field">Hours at service<input type="number" inputMode="decimal" step="0.1" min={0} value={f.hours} onChange={set("hours")} /></label>}
          {(meter === "cycles" || meter === "both") && <label className="field">Uses at service<input type="number" inputMode="numeric" min={0} value={f.cycles} onChange={set("cycles")} /></label>}
        </div>
        <label className="field">Work performed<textarea rows={2} value={f.work_performed} onChange={set("work_performed")} /></label>
        <b>Parts used</b>
        {parts.map((p, i) => (
          <div key={i} className="row" style={{ gap: 6, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 2 }}>{i === 0 ? "Part" : ""}<input value={p.description} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} /></label>
            <label className="field" style={{ flex: 1 }}>{i === 0 ? "Part no." : ""}<input value={p.part_number} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, part_number: e.target.value } : x)))} /></label>
            <label className="field" style={{ width: 64 }}>{i === 0 ? "Qty" : ""}<input type="number" inputMode="decimal" step="0.01" min={0.01} value={p.quantity} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} /></label>
            <label className="field" style={{ width: 84 }}>{i === 0 ? "Each" : ""}<input type="number" inputMode="decimal" step="0.01" min={0} value={p.unit_cost} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, unit_cost: e.target.value } : x)))} /></label>
            <button type="button" className="iconbtn" aria-label="Remove part" onClick={() => setParts(parts.filter((_, j) => j !== i))}><Trash2 size={18} /></button>
          </div>
        ))}
        <button type="button" className="btn small" onClick={() => setParts([...parts, { description: "", part_number: "", quantity: "1", unit_cost: "" }])}><Plus size={16} /> Add part</button>
        <div className="grid two">
          <label className="field">Labor cost<input type="number" inputMode="decimal" step="0.01" min={0} value={f.labor_cost} onChange={set("labor_cost")} /></label>
          <label className="field">Done by<input value={f.performed_by} onChange={set("performed_by")} /></label>
        </div>
        <label className="field">Notes<textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
        <div className="row wrap" style={{ gap: 6 }}>
          {photos.map((p) => p.thumb && <img key={p.id} src={url(p.thumb)} alt="" width={56} height={56} style={{ objectFit: "cover", borderRadius: 8 }} />)}
          <label className="btn small">Add photo / receipt<input type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => addPhoto(e.target.files)} /></label>
        </div>
        <button className="btn primary block" disabled={busy}>Save</button>
        {task && (
          <div className="row wrap" style={{ gap: 6 }}>
            <button type="button" className="btn small" disabled={busy} onClick={() => action("skip", { note: f.notes || null }, "Skipped this time")}>Skip this time</button>
            <button type="button" className="btn small" disabled={busy} onClick={() => setMore(more ? null : "defer")}>Defer…</button>
            <button type="button" className="btn small" disabled={busy} onClick={() => action("not-applicable", { value: true, note: f.notes || null }, "Marked not applicable")}>Not applicable</button>
          </div>
        )}
        {more === "defer" && (
          <div className="row" style={{ gap: 6, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 1 }}>Defer until<input type="date" min={today()} value={deferTo} onChange={(e) => setDeferTo(e.target.value)} /></label>
            <button type="button" className="btn" disabled={busy || !deferTo} onClick={() => action("defer", { until: deferTo, note: f.notes || null }, "Deferred")}>Defer</button>
          </div>
        )}
      </form>
    </Sheet>
  );
}

function TaskSheet({ itemId, task, onClose, onDone }: { itemId: number; task?: MaintTask; onClose: () => void; onDone: (d?: MaintenanceSummary) => void }) {
  const [f, setF] = useState({
    title: task?.title || "",
    instructions: task?.instructions || "",
    interval_days: task?.interval_days ? String(task.interval_days) : "",
    interval_hours: task?.interval_hours ? String(task.interval_hours) : "",
    interval_cycles: task?.interval_cycles ? String(task.interval_cycles) : "",
    next_due_on: task?.next_due_on || "",
    last_done_on: "",
    last_done_hours: "",
  });
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = {
      title: f.title,
      instructions: f.instructions || null,
      interval_days: num(f.interval_days),
      interval_hours: num(f.interval_hours),
      interval_cycles: num(f.interval_cycles),
    };
    if (task) {
      if (f.next_due_on !== (task.next_due_on || "")) body.next_due_on = f.next_due_on || null;
      const r = await run(() => api.patch(`maintenance-tasks/${task.id}`, body), "Task saved");
      if (r) onDone();
    } else {
      if (f.next_due_on) body.first_due_on = f.next_due_on;
      if (f.last_done_on) body.last_done_on = f.last_done_on;
      if (f.last_done_hours) body.last_done_hours = Number(f.last_done_hours);
      const r = await run(() => api.post(`items/${itemId}/maintenance-tasks`, body), "Task added");
      if (r) onDone();
    }
  };
  const remove = async () => {
    if (!task || !window.confirm(`Remove “${task.title}”? Its past service entries are kept.`)) return;
    await run(() => api.del(`maintenance-tasks/${task.id}`), "Task removed");
    onDone();
  };
  return (
    <Sheet title={task ? "Edit task" : "New maintenance task"} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="field">Task<input required value={f.title} onChange={set("title")} placeholder="Engine oil change" /></label>
        <b>Repeat every… <span className="muted" style={{ fontWeight: 400 }}>(whichever comes first; leave all empty for a one-off)</span></b>
        <div className="grid two">
          <label className="field">Days<input type="number" inputMode="numeric" min={1} value={f.interval_days} onChange={set("interval_days")} placeholder="365 = yearly" /></label>
          <label className="field">Operating hours<input type="number" inputMode="decimal" step="0.1" min={0.1} value={f.interval_hours} onChange={set("interval_hours")} /></label>
          <label className="field">Uses / tanks<input type="number" inputMode="numeric" min={1} value={f.interval_cycles} onChange={set("interval_cycles")} /></label>
          <label className="field">{task ? "Next due date" : "First due date (optional)"}<input type="date" value={f.next_due_on} onChange={set("next_due_on")} /></label>
          {!task && <label className="field">Last done (optional)<input type="date" max={today()} value={f.last_done_on} onChange={set("last_done_on")} /></label>}
          {!task && <label className="field">Hours when last done<input type="number" inputMode="decimal" step="0.1" min={0} value={f.last_done_hours} onChange={set("last_done_hours")} /></label>}
        </div>
        <label className="field">Instructions<textarea rows={3} value={f.instructions} onChange={set("instructions")} placeholder="Oil type, torque, steps…" /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
        {task && <button type="button" className="btn block" disabled={busy} onClick={remove}>Remove task</button>}
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------- dashboard page

export function MaintenancePage() {
  const me = useMe();
  const [tab, setTab] = useState<"overdue" | "due" | "missing" | "recent">("overdue");
  const [within, setWithin] = useState(30);
  const [owner, setOwner] = useState<"all" | "household" | "customer">("all");
  const d = useApi(() => api.get<MaintDashboard>("maintenance/dashboard", { within, owner }), [within, owner]);
  if (d.loading && !d.data) return <Spinner />;
  if (d.error || !d.data) return <div className="page"><ErrorBox error={d.error} /></div>;
  const x = d.data;
  const taskList = (rows: MaintTask[], empty: string) =>
    rows.length ? (
      <div className="list">
        {rows.map((t) => (
          <Link key={t.id} to={`/items/${t.item!.id}`}>
            <Wrench size={20} />
            <div className="grow">
              <div className="t">{t.item!.name} – {t.title}</div>
              <div className="s">{dueText(t)}</div>
            </div>
            <TaskBadge t={t} />
          </Link>
        ))}
      </div>
    ) : <div className="empty">{empty}</div>;
  const repairs = (x.repairs_in_progress?.length || 0) + (x.awaiting_pickup?.length || 0);
  return (
    <div className="page">
      {me.can_edit && (
        <div className="steps" style={{ marginBottom: 10 }}>
          {(["all", "household", "customer"] as const).map((o) => (
            <button key={o} className={owner === o ? "active" : ""} onClick={() => setOwner(o)}>{o === "all" ? "All equipment" : o === "household" ? "Mine" : "Customers'"}</button>
          ))}
        </div>
      )}
      {repairs > 0 && (
        <div className="card">
          <div className="card-title"><h2>Repairs</h2><Link className="btn small" to="/repairs">All jobs</Link></div>
          <div className="list">
            {[...(x.awaiting_pickup || []), ...(x.repairs_in_progress || [])].map((j) => (
              <Link key={j.id} to={`/repairs/${j.id}`}>
                <Wrench size={20} />
                <div className="grow"><div className="t">{j.number} · {j.item.name}</div><div className="s">{j.customer.name}</div></div>
                <span className={`badge ${j.awaiting_pickup ? "ok" : "blue"}`}>{j.awaiting_pickup ? "Ready for pickup" : j.status_label}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
      <div className="tabs">
        <button className={tab === "overdue" ? "active" : ""} onClick={() => setTab("overdue")}>Overdue ({x.overdue.length})</button>
        <button className={tab === "due" ? "active" : ""} onClick={() => setTab("due")}>Due soon ({x.due.length})</button>
        <button className={tab === "missing" ? "active" : ""} onClick={() => setTab("missing")}>No records ({x.no_records.length})</button>
        <button className={tab === "recent" ? "active" : ""} onClick={() => setTab("recent")}>Recent</button>
      </div>
      {tab === "overdue" && taskList(x.overdue, "Nothing overdue.")}
      {tab === "due" && (
        <>
          <div className="tabs">
            {[7, 30, 90].map((n) => <button key={n} className={within === n ? "active" : ""} onClick={() => setWithin(n)}>Next {n} days</button>)}
          </div>
          {taskList(x.due, `Nothing due in the next ${within} days.`)}
        </>
      )}
      {tab === "missing" && (x.no_records.length ? (
        <div className="list">
          {x.no_records.map((e) => (
            <Link key={e.id} to={`/items/${e.id}`}>
              <Wrench size={20} />
              <div className="grow"><div className="t">{e.name}</div><div className="s">{e.equipment_type || "Equipment"} · no service recorded</div></div>
            </Link>
          ))}
        </div>
      ) : <div className="empty">Every tracked item has service history.</div>)}
      {tab === "recent" && (x.recent.length ? (
        <ul className="list">{x.recent.map((e) => <ServiceRow key={e.id} e={e} currency={me.app.currency} showItem />)}</ul>
      ) : <div className="empty">No service recorded yet.</div>)}
      {x.equipment_count === 0 && (
        <p className="muted">To start, open an item such as a mower or generator and tap <b>Track maintenance</b>.</p>
      )}
    </div>
  );
}
