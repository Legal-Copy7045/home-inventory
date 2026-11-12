import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ClipboardList, Phone, Plus, Printer, Search, User, Wrench } from "lucide-react";
import {
  api,
  REPAIR_STATUS_LABELS,
  uploadPhoto,
  url,
  type Customer,
  type CustomerDetail,
  type Item,
  type MaintenanceSummary,
  type RepairJob,
  type RepairStatus,
} from "../api";
import { Empty, ErrorBox, Section, Sheet, Spinner, fmtDate, fmtWhen, money, useAction, useApi, useMe } from "../components/ui";
import { ServiceSheet } from "./Maintenance";

const STATUS_FLOW: RepairStatus[] = ["received", "awaiting_diagnosis", "awaiting_approval", "waiting_for_parts", "in_progress", "completed", "returned"];

export function JobBadge({ j }: { j: Pick<RepairJob, "status"> }) {
  const cls = j.status === "completed" ? "ok" : j.status === "returned" || j.status === "cancelled" ? "" : j.status === "waiting_for_parts" || j.status === "awaiting_approval" ? "warn" : "blue";
  return <span className={`badge ${cls}`}>{REPAIR_STATUS_LABELS[j.status]}</span>;
}

function JobRow({ j, showCustomer = true }: { j: RepairJob; showCustomer?: boolean }) {
  const me = useMe();
  return (
    <Link to={`/repairs/${j.id}`}>
      <ClipboardList size={20} />
      <div className="grow">
        <div className="t">{j.number} · {j.item.name}</div>
        <div className="s">{showCustomer ? `${j.customer.name} · ` : ""}received {fmtDate(j.received_on)}{j.reported_problem ? ` · ${j.reported_problem}` : ""}</div>
        {j.total > 0 && <div className="s">{money(j.total, j.currency || me.app.currency)}</div>}
      </div>
      <JobBadge j={j} />
    </Link>
  );
}

// ---------------------------------------------------------------- customers

export function CustomersPage() {
  const [q, setQ] = useState("");
  const list = useApi(() => api.get<Customer[]>("customers", { q: q || undefined }), [q]);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="row grow" style={{ gap: 6 }}>
          <Search size={18} />
          <input type="search" placeholder="Search name, phone or email" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
        </div>
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={18} /> Customer</button>
      </div>
      <ErrorBox error={list.error} />
      {list.loading && !list.data ? <Spinner /> : list.data?.length ? (
        <div className="list">
          {list.data.map((c) => (
            <Link key={c.id} to={`/customers/${c.id}`}>
              <User size={20} />
              <div className="grow">
                <div className="t">{c.name}</div>
                <div className="s">{[c.phone, c.email].filter(Boolean).join(" · ") || "No contact details"}</div>
              </div>
              <div className="s" style={{ textAlign: "right" }}>
                {c.equipment_count} item{c.equipment_count === 1 ? "" : "s"}
                {c.open_jobs ? <div><span className="badge blue">{c.open_jobs} open</span></div> : null}
              </div>
            </Link>
          ))}
        </div>
      ) : <Empty>No customers yet. Add the people whose equipment you repair.</Empty>}
      {adding && <CustomerSheet onClose={() => setAdding(false)} onDone={(c) => nav(`/customers/${c.id}`)} />}
    </div>
  );
}

function CustomerSheet({ customer, onClose, onDone }: { customer?: Customer; onClose: () => void; onDone: (c: CustomerDetail) => void }) {
  const [f, setF] = useState({ name: customer?.name || "", phone: customer?.phone || "", email: customer?.email || "", address: customer?.address || "", notes: customer?.notes || "" });
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const c = await run(() => (customer ? api.patch<CustomerDetail>(`customers/${customer.id}`, f) : api.post<CustomerDetail>("customers", f)), "Saved");
    if (c) onDone(c);
  };
  return (
    <Sheet title={customer ? "Edit customer" : "New customer"} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="field">Name<input required autoFocus value={f.name} onChange={set("name")} /></label>
        <div className="grid two">
          <label className="field">Phone<input type="tel" value={f.phone} onChange={set("phone")} /></label>
          <label className="field">Email<input type="email" value={f.email} onChange={set("email")} /></label>
        </div>
        <label className="field">Address (optional)<textarea rows={2} value={f.address} onChange={set("address")} /></label>
        <label className="field">Notes<textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

export function CustomerDetailPage() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const c = useApi(() => api.get<CustomerDetail>(`customers/${id}`), [id]);
  const [sheet, setSheet] = useState<null | "edit" | "job" | "equipment" | "contact">(null);
  const { busy, run } = useAction();
  if (c.loading && !c.data) return <Spinner />;
  if (c.error || !c.data) return <div className="page"><ErrorBox error={c.error} /></div>;
  const d = c.data;
  const done = (x?: CustomerDetail) => { if (x) c.setData(x); else c.reload(); setSheet(null); };
  const remove = async () => {
    if (!window.confirm(`Delete ${d.name}? Their equipment stays recorded as customer equipment.`)) return;
    try {
      await api.del(`customers/${d.id}`);
      nav("/customers");
    } catch (err) {
      await run(() => Promise.reject(err));
    }
  };
  return (
    <div className="page">
      <div className="owner-banner"><span><User size={18} /> Customer</span></div>
      <h1 style={{ margin: "0 0 4px" }}>{d.name}</h1>
      <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
        {d.phone && <a className="btn small" href={`tel:${d.phone}`}><Phone size={16} /> {d.phone}</a>}
        {d.email && <a className="btn small" href={`mailto:${d.email}`}>{d.email}</a>}
        <button className="btn small" onClick={() => setSheet("edit")}>Edit</button>
      </div>
      {d.address && <p className="muted" style={{ whiteSpace: "pre-line", marginTop: 0 }}>{d.address}</p>}
      {d.notes && <p style={{ whiteSpace: "pre-line" }}>{d.notes}</p>}

      <div className="quick" style={{ marginBottom: 14 }}>
        <button onClick={() => setSheet("job")}><span className="ico"><ClipboardList size={20} /></span>New repair job</button>
        <button onClick={() => setSheet("equipment")}><span className="ico"><Wrench size={20} /></span>Add equipment</button>
        <button onClick={() => setSheet("contact")}><span className="ico"><Phone size={20} /></span>Log contact</button>
      </div>

      <Section title={`Repair jobs (${d.jobs.length})`} tight>
        {d.jobs.length ? <div className="list">{d.jobs.map((j) => <JobRow key={j.id} j={j} showCustomer={false} />)}</div> : <p className="muted" style={{ padding: "0 16px" }}>No repair jobs yet.</p>}
      </Section>
      <Section title={`Equipment (${d.equipment.length})`} tight>
        {d.equipment.length ? (
          <div className="list">
            {d.equipment.map((e) => (
              <Link key={e.id} to={`/items/${e.id}`}>
                <Wrench size={20} />
                <div className="grow">
                  <div className="t">{e.name}</div>
                  <div className="s">{[e.manufacturer, e.model_number, e.serial_number && `S/N ${e.serial_number}`].filter(Boolean).join(" · ")}</div>
                </div>
              </Link>
            ))}
          </div>
        ) : <p className="muted" style={{ padding: "0 16px" }}>No equipment recorded.</p>}
      </Section>
      <Section title="Contact history">
        {d.contacts.length ? (
          <ul className="list">{d.contacts.map((x) => (
            <li key={x.id}><div className="grow"><div className="t" style={{ fontWeight: 500 }}>{x.note}</div><div className="s">{fmtWhen(x.contacted_at)}{x.channel ? ` · ${x.channel.replace("_", " ")}` : ""}</div></div></li>
          ))}</ul>
        ) : <p className="muted">Nothing logged.</p>}
      </Section>
      {me.is_admin || me.can_edit ? <button className="btn block" disabled={busy} onClick={remove}>Delete customer</button> : null}

      {sheet === "edit" && <CustomerSheet customer={d} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "job" && <NewJobSheet customer={d} onClose={() => setSheet(null)} onDone={(j) => nav(`/repairs/${j.id}`)} />}
      {sheet === "equipment" && <EquipmentSheet customer={d} onClose={() => setSheet(null)} onDone={() => done()} />}
      {sheet === "contact" && <ContactSheet customerId={d.id} onClose={() => setSheet(null)} onDone={done} />}
    </div>
  );
}

interface EquipmentForm { name: string; manufacturer: string; model_number: string; serial_number: string; description: string }
const emptyEquipment: EquipmentForm = { name: "", manufacturer: "", model_number: "", serial_number: "", description: "" };

function EquipmentFields({ f, setF }: { f: EquipmentForm; setF: (f: EquipmentForm) => void }) {
  const set = (k: keyof EquipmentForm) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <label className="field">Equipment<input required value={f.name} onChange={set("name")} placeholder="e.g. Toro Recycler 22 in mower" /></label>
      <div className="grid two">
        <label className="field">Make<input value={f.manufacturer} onChange={set("manufacturer")} /></label>
        <label className="field">Model<input value={f.model_number} onChange={set("model_number")} /></label>
        <label className="field">Serial number<input value={f.serial_number} onChange={set("serial_number")} /></label>
      </div>
    </>
  );
}

function clean<T extends object>(o: T) {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== "" && v !== null && v !== undefined));
}

function EquipmentSheet({ customer, onClose, onDone }: { customer: CustomerDetail; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<EquipmentForm>(emptyEquipment);
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (await run(() => api.post<Item>(`customers/${customer.id}/equipment`, clean(f)), "Equipment added")) onDone();
  };
  return (
    <Sheet title={`Equipment for ${customer.name}`} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <EquipmentFields f={f} setF={setF} />
        <button className="btn primary block" disabled={busy}>Add</button>
      </form>
    </Sheet>
  );
}

function ContactSheet({ customerId, onClose, onDone }: { customerId: number; onClose: () => void; onDone: (c: CustomerDetail) => void }) {
  const [channel, setChannel] = useState("phone");
  const [note, setNote] = useState("");
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const c = await run(() => api.post<CustomerDetail>(`customers/${customerId}/contacts`, { channel, note }), "Logged");
    if (c) onDone(c);
  };
  return (
    <Sheet title="Log contact" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="steps">
          {["phone", "text", "email", "in_person"].map((x) => <button type="button" key={x} className={channel === x ? "active" : ""} onClick={() => setChannel(x)}>{x.replace("_", " ")}</button>)}
        </div>
        <label className="field">Note<textarea required rows={3} autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Quoted $60, approved by phone" /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

export function NewJobSheet({ customer, itemId, onClose, onDone }: { customer?: CustomerDetail; itemId?: number; onClose: () => void; onDone: (j: RepairJob) => void }) {
  const customers = useApi(() => (customer ? Promise.resolve([] as Customer[]) : api.get<Customer[]>("customers")), [customer?.id]);
  const [customerId, setCustomerId] = useState<number | null>(customer?.id ?? null);
  const detail = useApi(() => (customerId ? api.get<CustomerDetail>(`customers/${customerId}`) : Promise.resolve(null)), [customerId]);
  const [equipment, setEquipment] = useState<string>(itemId ? String(itemId) : "new");
  const [eq, setEq] = useState<EquipmentForm>(emptyEquipment);
  const [f, setF] = useState({ reported_problem: "", condition_received: "", received_on: new Date().toISOString().slice(0, 10), estimate: "" });
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { customer_id: customerId, ...clean(f) };
    if (equipment === "new") body.new_equipment = clean(eq);
    else body.item_id = Number(equipment);
    const j = await run(() => api.post<RepairJob>("repair-jobs", body), "Repair job opened");
    if (j) onDone(j);
  };
  const owned = detail.data?.equipment || [];
  return (
    <Sheet title="New repair job" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        {!customer && (
          <label className="field">Customer
            <select required value={customerId ?? ""} onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Choose…</option>
              {customers.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <span className="hint">New customer? Add them on the <Link to="/customers">Customers</Link> page first.</span>
          </label>
        )}
        {customerId && (
          <label className="field">Equipment
            <select value={equipment} onChange={(e) => setEquipment(e.target.value)}>
              <option value="new">New equipment…</option>
              {owned.map((o) => <option key={o.id} value={o.id}>{o.name}{o.serial_number ? ` (${o.serial_number})` : ""}</option>)}
            </select>
          </label>
        )}
        {equipment === "new" && <EquipmentFields f={eq} setF={setEq} />}
        <label className="field">Reported problem<textarea rows={2} value={f.reported_problem} onChange={set("reported_problem")} placeholder="Won't start, surges…" /></label>
        <label className="field">Condition when received<textarea rows={2} value={f.condition_received} onChange={set("condition_received")} placeholder="Scratches, missing parts, fuel in tank…" /></label>
        <div className="grid two">
          <label className="field">Received<input type="date" value={f.received_on} onChange={set("received_on")} /></label>
          <label className="field">Estimate (optional)<input type="number" inputMode="decimal" step="0.01" min={0} value={f.estimate} onChange={set("estimate")} /></label>
        </div>
        <button className="btn primary block" disabled={busy || !customerId}>Open job</button>
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------- repair jobs

export function RepairsPage() {
  const [tab, setTab] = useState<"active" | "pickup" | "returned" | "all">("active");
  const jobs = useApi(() => api.get<RepairJob[]>("repair-jobs", { status: tab === "all" ? undefined : tab === "active" ? "open" : tab }), [tab]);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="row between wrap" style={{ marginBottom: 6 }}>
        <div className="tabs" style={{ marginBottom: 0 }}>
          <button className={tab === "active" ? "active" : ""} onClick={() => setTab("active")}>In progress</button>
          <button className={tab === "pickup" ? "active" : ""} onClick={() => setTab("pickup")}>Ready for pickup</button>
          <button className={tab === "returned" ? "active" : ""} onClick={() => setTab("returned")}>Returned</button>
          <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>All</button>
        </div>
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={18} /> Job</button>
      </div>
      <ErrorBox error={jobs.error} />
      {jobs.loading && !jobs.data ? <Spinner /> : jobs.data?.length ? (
        <div className="list" style={{ marginTop: 10 }}>{jobs.data.map((j) => <JobRow key={j.id} j={j} />)}</div>
      ) : <Empty>No repair jobs here.</Empty>}
      {adding && <NewJobSheet onClose={() => setAdding(false)} onDone={(j) => nav(`/repairs/${j.id}`)} />}
    </div>
  );
}

export function RepairJobPage() {
  const { id } = useParams();
  const me = useMe();
  const job = useApi(() => api.get<RepairJob>(`repair-jobs/${id}`), [id]);
  const [sheet, setSheet] = useState<null | "work" | "details" | "status">(null);
  const [nextStatus, setNextStatus] = useState<RepairStatus | null>(null);
  const { busy, run } = useAction();
  if (job.loading && !job.data) return <Spinner />;
  if (job.error || !job.data) return <div className="page"><ErrorBox error={job.error} /></div>;
  const j = job.data;
  const cur = me.app.currency;
  const done = (x?: RepairJob) => { if (x) job.setData(x); else job.reload(); setSheet(null); };
  const addPhoto = async (files: FileList | null) => {
    for (const file of Array.from(files || [])) await run(() => uploadPhoto(file, { repair_job_id: j.id, kind: file.type === "application/pdf" ? "receipt" : "photo" }));
    job.reload();
  };
  const emptySummary: MaintenanceSummary = { profile: null, meters: { hours: null, cycles: null }, readings: [], tasks: [], history: [] };
  return (
    <div className="page">
      <div className="owner-banner">
        <span><User size={18} /> {j.customer.name}'s equipment</span>
        <Link className="btn small no-print" to={`/repairs/${j.id}/print`}><Printer size={16} /> Work order</Link>
      </div>
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}>{j.number} · {j.item.name}</h1>
        <JobBadge j={j} />
      </div>
      <p className="muted" style={{ marginTop: 4 }}>
        <Link to={`/customers/${j.customer.id}`}>{j.customer.name}</Link>
        {j.customer.phone ? <> · <a href={`tel:${j.customer.phone}`}>{j.customer.phone}</a></> : null}
        {j.item.id ? <> · <Link to={`/items/${j.item.id}`}>equipment page</Link></> : null}
      </p>

      <Section title="Status">
        <div className="steps">
          {STATUS_FLOW.map((s) => (
            <button key={s} className={j.status === s ? "active" : ""} disabled={busy || j.status === s} onClick={() => { setNextStatus(s); setSheet("status"); }}>
              {REPAIR_STATUS_LABELS[s]}
            </button>
          ))}
        </div>
        {j.status !== "cancelled" && j.status !== "returned" && (
          <button className="btn small" style={{ marginTop: 10 }} onClick={() => { setNextStatus("cancelled"); setSheet("status"); }}>Cancel job</button>
        )}
      </Section>

      <div className="quick" style={{ marginBottom: 14 }}>
        <button onClick={() => setSheet("work")} disabled={!j.item.id}><span className="ico"><Wrench size={20} /></span>Record work</button>
        <button onClick={() => setSheet("details")}><span className="ico"><ClipboardList size={20} /></span>Diagnosis &amp; charges</button>
        <label className="btn" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="ico"><Plus size={20} /></span>Add photo / receipt
          <input type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => addPhoto(e.target.files)} />
        </label>
      </div>

      <Section title="Job">
        <dl className="kv">
          <dt>Received</dt><dd>{fmtDate(j.received_on)}</dd>
          {j.reported_problem && (<><dt>Problem</dt><dd style={{ whiteSpace: "pre-line" }}>{j.reported_problem}</dd></>)}
          {j.condition_received && (<><dt>Condition</dt><dd style={{ whiteSpace: "pre-line" }}>{j.condition_received}</dd></>)}
          {j.diagnosis && (<><dt>Diagnosis</dt><dd style={{ whiteSpace: "pre-line" }}>{j.diagnosis}</dd></>)}
          {j.approval_notes && (<><dt>Approval</dt><dd style={{ whiteSpace: "pre-line" }}>{j.approval_notes}</dd></>)}
          {j.estimate !== null && (<><dt>Estimate</dt><dd>{money(j.estimate, j.currency || cur)}</dd></>)}
          <dt>Parts</dt><dd>{money(j.parts_charge || 0, j.currency || cur)}</dd>
          <dt>Labor</dt><dd>{money(j.labor_charge || 0, j.currency || cur)}</dd>
          <dt>Total</dt><dd><b>{money(j.total, j.currency || cur)}</b></dd>
          {j.completed_on && (<><dt>Completed</dt><dd>{fmtDate(j.completed_on)}</dd></>)}
          {j.returned_on && (<><dt>Returned</dt><dd>{fmtDate(j.returned_on)}</dd></>)}
        </dl>
      </Section>

      <Section title="Work performed">
        {j.service?.length ? (
          <ul className="list">
            {j.service.map((e) => (
              <li key={e.id}>
                <Wrench size={18} />
                <div className="grow">
                  <div className="t">{e.title}</div>
                  <div className="s">{fmtDate(e.performed_on)}{e.performed_by ? ` · ${e.performed_by}` : ""}</div>
                  {e.work_performed && <div className="s" style={{ whiteSpace: "pre-line" }}>{e.work_performed}</div>}
                  {e.parts.length > 0 && <div className="s">Parts: {e.parts.map((p) => `${p.quantity !== 1 ? `${p.quantity} × ` : ""}${p.description}${p.unit_cost ? ` @ ${money(p.unit_cost, j.currency || cur)}` : ""}`).join(", ")}</div>}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="muted">Nothing recorded yet.</p>}
      </Section>

      {!!j.attachments?.length && (
        <Section title="Photos & documents">
          <div className="row wrap" style={{ gap: 8 }}>
            {j.attachments.map((a) => (
              <a key={a.id} href={url(a.url)} target="_blank" rel="noreferrer">
                {a.thumb_url ? <img src={url(a.thumb_url)} alt={a.caption || ""} width={88} height={88} style={{ objectFit: "cover", borderRadius: 10 }} /> : <span className="badge">{a.original_name || a.kind}</span>}
              </a>
            ))}
          </div>
        </Section>
      )}

      <Section title="Status history">
        <ul className="list">
          {j.history?.slice().reverse().map((h, i) => (
            <li key={i}><div className="grow"><div className="t" style={{ fontWeight: 500 }}>{h.to_label}{h.note ? ` – ${h.note}` : ""}</div><div className="s">{fmtWhen(h.ts)}{h.user ? ` · ${h.user}` : ""}</div></div></li>
          ))}
        </ul>
      </Section>

      {sheet === "work" && j.item.id && (
        <ServiceSheet itemId={j.item.id} summary={emptySummary} postTo={`repair-jobs/${j.id}/service`} title={`Work on ${j.number}`} onClose={() => setSheet(null)} onDone={done} />
      )}
      {sheet === "details" && <JobDetailsSheet j={j} onClose={() => setSheet(null)} onDone={done} />}
      {sheet === "status" && nextStatus && <StatusSheet j={j} status={nextStatus} onClose={() => setSheet(null)} onDone={done} />}
    </div>
  );
}

function StatusSheet({ j, status, onClose, onDone }: { j: RepairJob; status: RepairStatus; onClose: () => void; onDone: (j: RepairJob) => void }) {
  const [note, setNote] = useState("");
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const x = await run(() => api.post<RepairJob>(`repair-jobs/${j.id}/status`, { status, note: note || null }), REPAIR_STATUS_LABELS[status]);
    if (x) onDone(x);
  };
  return (
    <Sheet title={`${j.number}: ${REPAIR_STATUS_LABELS[status]}`} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="field">Note (optional)<input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder={status === "returned" ? "Collected by…" : status === "awaiting_approval" ? "Quoted $…" : ""} /></label>
        <button className="btn primary block" disabled={busy}>Set to {REPAIR_STATUS_LABELS[status]}</button>
      </form>
    </Sheet>
  );
}

function JobDetailsSheet({ j, onClose, onDone }: { j: RepairJob; onClose: () => void; onDone: (j: RepairJob) => void }) {
  const s = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));
  const [f, setF] = useState({
    reported_problem: j.reported_problem || "",
    condition_received: j.condition_received || "",
    diagnosis: j.diagnosis || "",
    approval_notes: j.approval_notes || "",
    estimate: s(j.estimate),
    labor_charge: s(j.labor_charge_set),
    parts_charge: s(j.parts_charge_set),
    notes: j.notes || "",
  });
  const { busy, run } = useAction();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const money = (v: string) => (v === "" ? null : v);
    const x = await run(() => api.patch<RepairJob>(`repair-jobs/${j.id}`, {
      ...f, estimate: money(f.estimate), labor_charge: money(f.labor_charge), parts_charge: money(f.parts_charge),
    }), "Saved");
    if (x) onDone(x);
  };
  return (
    <Sheet title="Diagnosis & charges" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="field">Reported problem<textarea rows={2} value={f.reported_problem} onChange={set("reported_problem")} /></label>
        <label className="field">Condition when received<textarea rows={2} value={f.condition_received} onChange={set("condition_received")} /></label>
        <label className="field">Diagnosis<textarea rows={3} value={f.diagnosis} onChange={set("diagnosis")} /></label>
        <label className="field">Approval<textarea rows={2} value={f.approval_notes} onChange={set("approval_notes")} placeholder="Approved by phone, up to $80" /></label>
        <div className="grid two">
          <label className="field">Estimate<input type="number" inputMode="decimal" step="0.01" min={0} value={f.estimate} onChange={set("estimate")} /></label>
          <label className="field">Labor charge<input type="number" inputMode="decimal" step="0.01" min={0} value={f.labor_charge} onChange={set("labor_charge")} /><span className="hint">Empty = sum of labor in recorded work</span></label>
          <label className="field">Parts charge<input type="number" inputMode="decimal" step="0.01" min={0} value={f.parts_charge} onChange={set("parts_charge")} /><span className="hint">Empty = cost of parts recorded</span></label>
        </div>
        <label className="field">Notes<textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}

export function WorkOrderPage() {
  const { id } = useParams();
  const me = useMe();
  const job = useApi(() => api.get<RepairJob>(`repair-jobs/${id}`), [id]);
  if (job.loading && !job.data) return <Spinner />;
  if (job.error || !job.data) return <div className="page"><ErrorBox error={job.error} /></div>;
  const j = job.data;
  const cur = j.currency || me.app.currency;
  const parts = (j.service || []).flatMap((e) => e.parts.map((p) => ({ ...p, when: e.performed_on })));
  return (
    <div className="page workorder">
      <div className="row between no-print" style={{ marginBottom: 12 }}>
        <Link to={`/repairs/${j.id}`}>← Back to job</Link>
        <button className="btn primary" onClick={() => window.print()}><Printer size={18} /> Print</button>
      </div>
      <div className="card">
        <div className="row between wrap">
          <div>
            <h1>Repair work order {j.number}</h1>
            <div className="muted">Received {fmtDate(j.received_on)}{j.completed_on ? ` · completed ${fmtDate(j.completed_on)}` : ""}{j.returned_on ? ` · returned ${fmtDate(j.returned_on)}` : ""}</div>
          </div>
          <div><b>{REPAIR_STATUS_LABELS[j.status]}</b></div>
        </div>
        <div className="grid two" style={{ marginTop: 14 }}>
          <div>
            <b>Customer</b>
            <div>{j.customer.name}</div>
            {j.customer.phone && <div>{j.customer.phone}</div>}
            {j.customer.email && <div>{j.customer.email}</div>}
            {j.customer.address && <div style={{ whiteSpace: "pre-line" }}>{j.customer.address}</div>}
          </div>
          <div>
            <b>Equipment</b>
            <div>{j.item.name}</div>
            {(j.item.manufacturer || j.item.model_number) && <div>{[j.item.manufacturer, j.item.model_number].filter(Boolean).join(" ")}</div>}
            {j.item.serial_number && <div>S/N {j.item.serial_number}</div>}
          </div>
        </div>
        {j.reported_problem && <><h3>Reported problem</h3><p style={{ whiteSpace: "pre-line" }}>{j.reported_problem}</p></>}
        {j.condition_received && <><h3>Condition when received</h3><p style={{ whiteSpace: "pre-line" }}>{j.condition_received}</p></>}
        {j.diagnosis && <><h3>Diagnosis</h3><p style={{ whiteSpace: "pre-line" }}>{j.diagnosis}</p></>}
        {!!j.service?.length && (
          <>
            <h3>Work performed</h3>
            {j.service.map((e) => <p key={e.id}><b>{fmtDate(e.performed_on)} – {e.title}.</b> {e.work_performed}</p>)}
          </>
        )}
        {parts.length > 0 && (
          <>
            <h3>Parts</h3>
            <table>
              <thead><tr><th>Part</th><th>Part no.</th><th className="num">Qty</th><th className="num">Each</th><th className="num">Amount</th></tr></thead>
              <tbody>
                {parts.map((p, i) => (
                  <tr key={i}><td>{p.description}</td><td>{p.part_number || ""}</td><td className="num">{p.quantity}</td>
                    <td className="num">{p.unit_cost !== null && p.unit_cost !== undefined ? money(p.unit_cost, cur) : ""}</td>
                    <td className="num">{p.unit_cost !== null && p.unit_cost !== undefined ? money(p.unit_cost * p.quantity, cur) : ""}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <table style={{ marginTop: 14 }}>
          <tbody>
            <tr><td>Parts</td><td className="num">{money(j.parts_charge || 0, cur)}</td></tr>
            <tr><td>Labor</td><td className="num">{money(j.labor_charge || 0, cur)}</td></tr>
            <tr><td><b>Total</b></td><td className="num"><b>{money(j.total, cur)}</b></td></tr>
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 18 }}>Customer signature ____________________________ Date ____________</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- on the item page

export function CustomerItemSection({ item, edit, onChanged }: { item: Item; edit: boolean; onChanged: () => void }) {
  const me = useMe();
  const jobs = useApi(() => (item.owner && me.can_edit ? api.get<RepairJob[]>(`items/${item.id}/repair-jobs`) : Promise.resolve([] as RepairJob[])), [item.id, item.owner?.customer_id]);
  const [sheet, setSheet] = useState<null | "job" | "owner">(null);
  const nav = useNavigate();
  return (
    <>
      {item.owner && (
        <div className="owner-banner">
          <span><User size={18} /> {item.owner.name ? <>Belongs to <Link to={`/customers/${item.owner.customer_id}`}>{item.owner.name}</Link></> : "Customer equipment"}</span>
          {edit && <span className="row" style={{ gap: 6 }}>
            <button className="btn small" onClick={() => setSheet("job")}><ClipboardList size={16} /> Start repair job</button>
            <button className="btn small" onClick={() => setSheet("owner")}>Change owner</button>
          </span>}
        </div>
      )}
      {item.owner && !!jobs.data?.length && (
        <Section title="Repair jobs" tight>
          <div className="list">{jobs.data.map((j) => <JobRow key={j.id} j={j} showCustomer={false} />)}</div>
        </Section>
      )}
      {!item.owner && edit && sheet === null && (
        <p className="muted" style={{ fontSize: ".85rem" }}>
          Household item. <button className="linkish" onClick={() => setSheet("owner")}>Belongs to someone else?</button>
        </p>
      )}
      {sheet === "job" && item.owner?.customer_id && (
        <CustomerJobLoader customerId={item.owner.customer_id} itemId={item.id} onClose={() => setSheet(null)} onDone={(j) => nav(`/repairs/${j.id}`)} />
      )}
      {sheet === "owner" && <OwnerSheet item={item} onClose={() => setSheet(null)} onDone={() => { setSheet(null); onChanged(); }} />}
    </>
  );
}

function CustomerJobLoader({ customerId, itemId, onClose, onDone }: { customerId: number; itemId: number; onClose: () => void; onDone: (j: RepairJob) => void }) {
  const c = useApi(() => api.get<CustomerDetail>(`customers/${customerId}`), [customerId]);
  if (!c.data) return null;
  return <NewJobSheet customer={c.data} itemId={itemId} onClose={onClose} onDone={onDone} />;
}

function OwnerSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: () => void }) {
  const customers = useApi(() => api.get<Customer[]>("customers"), []);
  const [choice, setChoice] = useState<string>(item.owner?.customer_id ? String(item.owner.customer_id) : "");
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const target = choice ? customers.data?.find((c) => String(c.id) === choice)?.name : "the household";
    if (!window.confirm(`Change the owner of ${item.name} to ${target}?`)) return;
    if (await run(() => api.post(`items/${item.id}/owner`, { customer_id: choice ? Number(choice) : null, confirm: true }), "Owner changed")) onDone();
  };
  return (
    <Sheet title="Who owns this?" onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <p className="muted" style={{ marginTop: 0 }}>Customer equipment is kept out of your household totals, value and shopping lists.</p>
        <label className="field">Owner
          <select value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">Household (mine)</option>
            {customers.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
    </Sheet>
  );
}
