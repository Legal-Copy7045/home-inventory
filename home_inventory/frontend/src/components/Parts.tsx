import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Check, Plus, ShoppingCart, Trash2, X } from "lucide-react";
import {
  api,
  uploadPhoto,
  type Attachment,
  type ItemParts,
  type ManualPart,
  type ManualReading,
  type ManualService,
  type PartBrief,
  type PartDetails,
  type ReadinessLine,
  type ReadinessOverview,
  type Readiness,
} from "../api";
import { ErrorBox, Section, Sheet, Spinner, useAction, useApi, usePoll } from "./ui";

// ---------------------------------------------------------------- small bits

const READY: Record<Readiness, [string, string]> = {
  in_stock: ["ok", "In stock"],
  partial: ["warn", "Not enough"],
  missing: ["danger", "None in stock"],
  uncertain: ["blue", "Check"],
};

export function ReadyBadge({ status }: { status: Readiness }) {
  const [cls, label] = READY[status];
  return <span className={`badge ${cls}`}>{label}</span>;
}

const amount = (q: number | null | undefined, unit: string | null | undefined) =>
  q === null || q === undefined ? "" : `${+q.toFixed(3)}${unit ? ` ${unit}` : q === 1 ? "" : " ×"}`;

export function partName(p: PartBrief) {
  return p.model_number && !p.name.includes(p.model_number) ? `${p.name} (${p.model_number})` : p.name;
}

function why(line: ReadinessLine): string {
  if (line.status === "in_stock") return `have ${amount(line.have, line.unit)}`;
  if (line.status === "uncertain") {
    return line.have_uncertain
      ? `${amount(line.have_uncertain, line.unit)} might do – confirm it's equivalent`
      : "sizes can't be compared – add the pack size";
  }
  return line.have ? `have ${amount(line.have, line.unit)}, short ${amount(line.short, line.unit)}` : `short ${amount(line.short, line.unit)}`;
}

/** One line under a task: "0.55 L Engine oil 10W-30 · In stock". */
export function TaskPartsLine({ rows, onClick }: { rows: ReadinessLine[]; onClick?: () => void }) {
  if (!rows.length) return onClick ? <button className="btn small ghost" onClick={onClick} style={{ padding: "2px 6px" }}><Plus size={14} /> Parts</button> : null;
  return (
    <div className="s" onClick={onClick} style={{ cursor: onClick ? "pointer" : undefined, display: "flex", flexWrap: "wrap", gap: "4px 10px" }}>
      {rows.map((r) => (
        <span key={r.task_part_id}>
          {amount(r.quantity, r.unit)} {r.product.name} <ReadyBadge status={r.status} />
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- picking a part

function PartPicker({ onPick }: { onPick: (p: PartBrief) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<PartBrief[]>([]);
  const [mode, setMode] = useState<null | "standard" | "part">(null);
  const [f, setF] = useState({ name: "", spec: "", unit: "", manufacturer: "", part_number: "" });
  const { busy, run } = useAction();
  useEffect(() => {
    if (q.trim().length < 2) { setFound([]); return; }
    const t = setTimeout(() => api.get<PartBrief[]>("parts/search", { q }).then(setFound).catch(() => setFound([])), 250);
    return () => clearTimeout(t);
  }, [q]);
  const create = async () => {
    const p = mode === "standard"
      ? await run(() => api.post<PartBrief>("parts/standards", { name: f.name, specs: f.spec ? { Specification: f.spec } : null, unit: f.unit || null }), "Standard added")
      : await run(() => api.post<PartBrief>("parts/products", { name: f.name, manufacturer: f.manufacturer || null, part_number: f.part_number || null }), "Part added");
    if (p) { onPick(p); setMode(null); setQ(""); }
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search parts, part numbers or standards (e.g. 10W-30)" />
      {found.length > 0 && (
        <ul className="list">
          {found.map((p) => (
            <li key={p.id} onClick={() => { onPick(p); setQ(""); }} style={{ cursor: "pointer" }}>
              <div className="grow"><div className="t">{partName(p)} {p.is_generic && <span className="badge blue">Standard</span>}</div></div>
              <Plus size={18} />
            </li>
          ))}
        </ul>
      )}
      <div className="row wrap" style={{ gap: 6 }}>
        <button type="button" className="btn small" onClick={() => { setMode("standard"); setF({ ...f, name: q }); }}>New standard (any brand)</button>
        <button type="button" className="btn small" onClick={() => { setMode("part"); setF({ ...f, name: q }); }}>New specific part</button>
      </div>
      {mode && (
        <div className="stack notice" style={{ gap: 6 }}>
          <label className="field">{mode === "standard" ? "Standard" : "Part"}<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={mode === "standard" ? "Engine oil SAE 10W-30" : "Air filter"} /></label>
          {mode === "standard" ? (
            <div className="grid two">
              <label className="field">Specification<input value={f.spec} onChange={(e) => setF({ ...f, spec: e.target.value })} placeholder="10W-30" /></label>
              <label className="field">Measured in<input value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} placeholder="L, g or empty for pieces" /></label>
            </div>
          ) : (
            <div className="grid two">
              <label className="field">Make<input value={f.manufacturer} onChange={(e) => setF({ ...f, manufacturer: e.target.value })} /></label>
              <label className="field">Part number<input value={f.part_number} onChange={(e) => setF({ ...f, part_number: e.target.value })} /></label>
            </div>
          )}
          <button type="button" className="btn primary small" disabled={busy || !f.name.trim()} onClick={create}>Add</button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- a task's parts

interface PartRow { product: PartBrief; quantity: string; unit: string; allow_equivalent: boolean; note: string }

export function TaskPartsSheet({ taskId, title, rows, onClose, onDone }: { taskId: number; title: string; rows: ReadinessLine[]; onClose: () => void; onDone: () => void }) {
  const [list, setList] = useState<PartRow[]>(rows.map((r) => ({
    product: r.product, quantity: String(r.quantity), unit: r.unit || "", allow_equivalent: r.allow_equivalent ?? true, note: r.note || "",
  })));
  const { busy, run } = useAction();
  const set = (i: number, patch: Partial<PartRow>) => setList(list.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const save = async () => {
    const body = { parts: list.map((r) => ({ product_id: r.product.id, quantity: Number(r.quantity) || 1, unit: r.unit || null, allow_equivalent: r.allow_equivalent, note: r.note || null, role: r.note || null })) };
    if (await run(() => api.put(`maintenance-tasks/${taskId}/parts`, body), "Parts saved")) onDone();
  };
  return (
    <Sheet title={`Parts for ${title}`} onClose={onClose}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>What this service uses each time. A <b>standard</b> (e.g. 10W-30 oil) accepts any brand that meets it. Leave the unit empty to count pieces.</p>
        {list.map((r, i) => (
          <div key={r.product.id} className="notice stack" style={{ gap: 6 }}>
            <div className="row between"><b>{partName(r.product)} {r.product.is_generic && <span className="badge blue">Standard</span>}</b>
              <button className="iconbtn" aria-label="Remove" onClick={() => setList(list.filter((_, j) => j !== i))}><Trash2 size={18} /></button></div>
            <div className="grid two">
              <label className="field">Amount<input type="number" inputMode="decimal" step="0.01" min={0.01} value={r.quantity} onChange={(e) => set(i, { quantity: e.target.value })} /></label>
              <label className="field">Unit<input value={r.unit} onChange={(e) => set(i, { unit: e.target.value })} placeholder="L, ml, g… or empty" /></label>
            </div>
            <label className="field">What it's for<input value={r.note} onChange={(e) => set(i, { note: e.target.value })} placeholder="Engine oil, spark plug…" /></label>
            {!r.product.is_generic && (
              <label className="row"><input type="checkbox" checked={r.allow_equivalent} onChange={(e) => set(i, { allow_equivalent: e.target.checked })} />
                Or an equivalent (anything meeting the same standard, or its replacement part)</label>
            )}
          </div>
        ))}
        <b>Add a part</b>
        <PartPicker onPick={(p) => !list.some((x) => x.product.id === p.id) && setList([...list, { product: p, quantity: "1", unit: p.content_unit || "", allow_equivalent: true, note: "" }])} />
        <button className="btn primary block" disabled={busy} onClick={save}>Save</button>
      </div>
    </Sheet>
  );
}

/** Rows to prefill "Parts used" when a task is completed. */
export function partsForService(rows: ReadinessLine[]) {
  return rows.map((r) => {
    const src = r.sources.find((s) => s.sure);
    const item = src?.items[0];
    const measured = !!r.unit;
    return {
      description: src ? src.product.name : r.product.name,
      part_number: (src ? src.product.model_number : r.product.model_number) || "",
      quantity: String(r.quantity),
      unit_cost: "",
      item_id: item?.id ?? null,
      // Pieces come out of stock automatically; a bottle only when it's used up.
      deduct_stock: !!item && !measured,
      measured,
      stock_label: item ? `${src!.product.name}${item.label ? ` (${item.label})` : ""}` : null,
    };
  });
}

// ---------------------------------------------------------------- reading the manual

export function ManualSheet({ itemId, onClose, onDone }: { itemId: number; onClose: () => void; onDone: () => void }) {
  const item = useApi(() => api.get<{ attachments: Attachment[] }>(`items/${itemId}`), [itemId]);
  const past = useApi(() => api.get<ManualReading[]>(`items/${itemId}/manual-readings`), [itemId]);
  // What AI recognised each file as (every upload is recognised), so a wrong file stands out.
  const recognised = useApi(() => api.get<{ attachment_id: number; status: string; result: { document_type: string; title: string } | null }[]>(`items/${itemId}/document-readings`), [itemId, item.data?.attachments?.length]);
  const docType = (id: number) => recognised.data?.find((r) => r.attachment_id === id);
  const checking = recognised.data?.some((r) => r.status === "pending");
  usePoll(!!checking, recognised.reload);
  const [chosen, setChosen] = useState<number[]>([]);
  const [pages, setPages] = useState("");
  const [reading, setReading] = useState<ManualReading | null>(null);
  const { busy, run } = useAction();
  useEffect(() => {
    // Pick up a reading that's still running or waiting for review.
    const open = past.data?.find((r) => r.status === "pending" || r.status === "ready");
    if (open && !reading) setReading(open);
  }, [past.data]); // eslint-disable-line react-hooks/exhaustive-deps
  // Reading runs in the background (it can take minutes): check every few seconds.
  usePoll(reading?.status === "pending", () => {
    if (reading) api.get<ManualReading>(`manual-readings/${reading.id}`).then((r) => r.status !== "pending" && setReading(r)).catch(() => undefined);
  });
  const files = (item.data?.attachments || []).filter((a) => a.mime_type === "application/pdf" || a.mime_type.startsWith("image/"));
  const upload = async (list: FileList | null) => {
    for (const file of Array.from(list || [])) {
      const a = await run(() => uploadPhoto(file, { kind: "manual", item_id: itemId }));
      if (a) setChosen((c) => [...c, a.id]);
    }
    item.reload();
  };
  const read = async () => {
    const r = await run(() => api.post<ManualReading>(`items/${itemId}/manual-readings`, { attachment_ids: chosen, pages: pages.trim() || null }));
    if (r) setReading(r);
  };
  if (reading?.status === "ready" && reading.result) {
    return <ManualReview reading={reading} onClose={onClose} onDone={onDone} onDiscard={() => setReading(null)} />;
  }
  return (
    <Sheet title="Read the manual" onClose={onClose}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          The AI reads the maintenance schedule, the parts and consumables, and what each service needs. You check everything before anything is saved.
          A PDF manual works best. For a paper or scanned manual, photograph the maintenance pages.
        </p>
        {reading?.status === "failed" && <div className="error">{reading.error}</div>}
        {reading?.status === "pending" && (
          <div className="notice stack" style={{ gap: 6 }}>
            <Spinner />
            <b>Reading the manual…</b>
            <span>This can take a few minutes for a long manual. You can close this and come back: the result waits on this item.</span>
          </div>
        )}
        {item.loading ? <Spinner /> : (
          <ul className="list">
            {files.map((a) => (
              <li key={a.id}>
                <label className="row grow" style={{ gap: 8 }}>
                  <input type="checkbox" checked={chosen.includes(a.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, a.id] : chosen.filter((x) => x !== a.id))} />
                  <span className="grow">{a.original_name || a.caption || (a.mime_type === "application/pdf" ? "PDF" : "Photo")} <span className="muted">· {a.kind}</span>
                    {(() => {
                      const r = docType(a.id);
                      if (r?.status === "pending") return <span className="muted"> · AI is checking what it is…</span>;
                      if (r?.result && r.result.document_type !== "manual") {
                        return <span className="badge warn" style={{ marginLeft: 6 }}>Looks like: {r.result.document_type.replace("_", " ")}{r.result.title ? ` (${r.result.title})` : ""}</span>;
                      }
                      return null;
                    })()}
                  </span>
                </label>
              </li>
            ))}
            {!files.length && <li className="muted">No manual attached yet.</li>}
          </ul>
        )}
        <label className="btn">Add the manual (PDF) or photos of its pages<input type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => upload(e.target.files)} /></label>
        {reading?.status !== "pending" && (
          <label className="field">Pages to read (optional)
            <input value={pages} onChange={(e) => setPages(e.target.value)} placeholder="e.g. 18-26" inputMode="numeric" />
            <span className="hint">For a long or scanned PDF: the pages with the maintenance schedule and parts. Scanned manuals are read page by page (the first 12 if you leave this empty).</span>
          </label>
        )}
        {reading?.status !== "pending" && (
          <button className="btn primary block" disabled={busy || !chosen.length} onClick={read}>
            <BookOpen size={18} /> Read it
          </button>
        )}
      </div>
    </Sheet>
  );
}

type PartChoice = ManualPart & { include: boolean; use_match: boolean; is_generic: boolean; spec_value: string };
type ServiceChoice = ManualService & { include: boolean; task_id: number | null };

function interval(s: ManualService) {
  const p: string[] = [];
  if (s.interval_hours) p.push(`${s.interval_hours} h`);
  if (s.interval_days) p.push(s.interval_days % 365 === 0 ? `${s.interval_days / 365} yr` : s.interval_days % 30 === 0 ? `${s.interval_days / 30} mo` : `${s.interval_days} d`);
  if (s.interval_cycles) p.push(`${s.interval_cycles} uses`);
  const every = p.length ? `every ${p.join(" or ")}` : "once / as needed";
  return s.first_at_hours ? `first at ${s.first_at_hours} h, then ${every}` : every;
}

function ManualReview({ reading, onClose, onDone, onDiscard }: { reading: ManualReading; onClose: () => void; onDone: () => void; onDiscard: () => void }) {
  const res = reading.result!;
  const [parts, setParts] = useState<PartChoice[]>(res.parts.map((p) => ({
    ...p, include: true, use_match: !!p.match, is_generic: p.proposed_generic,
    spec_value: Object.values(p.proposed_specs)[0] || "",
  })));
  const [services, setServices] = useState<ServiceChoice[]>(res.services.map((s) => ({ ...s, include: true, task_id: s.task_match?.id ?? null })));
  const [specs, setSpecs] = useState(res.specs.map((s) => ({ ...s, include: true })));
  const { busy, run } = useAction();
  const setP = (i: number, patch: Partial<PartChoice>) => setParts(parts.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const setS = (i: number, patch: Partial<ServiceChoice>) => setServices(services.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const byKey = Object.fromEntries(parts.map((p) => [p.key, p]));
  const apply = async () => {
    const body = {
      parts: parts.map((p) => ({
        key: p.key, include: p.include, product_id: p.use_match && p.match ? p.match.id : null,
        name: p.name, role: p.role, manufacturer: p.manufacturer, part_number: p.part_number, specification: p.specification,
        is_generic: p.is_generic, specs: p.is_generic && p.spec_value ? { [Object.keys(p.proposed_specs)[0] || "Specification"]: p.spec_value } : null,
        quantity: p.quantity, unit: p.unit, page: p.page,
      })),
      services: services.map((s) => ({
        include: s.include, title: s.title, task_id: s.task_id, interval_hours: s.interval_hours, interval_days: s.interval_days,
        interval_cycles: s.interval_cycles, first_at_hours: s.first_at_hours, instructions: s.instructions,
        needs: s.needs.filter((n) => byKey[n.key]?.include), page: s.page,
      })),
      specs: specs.map((s) => ({ include: s.include, label: s.label, value: s.value })),
    };
    const r = await run(() => api.post<{ summary: Record<string, number> }>(`manual-readings/${reading.id}/apply`, body));
    if (r) {
      const x = r.summary;
      window.alert(`Saved: ${x.tasks_created} new tasks, ${x.tasks_updated} updated, ${x.parts_linked} parts and consumables, ${x.specs_added} specifications.`);
      onDone();
    }
  };
  const discard = async () => {
    if (await run(() => api.post(`manual-readings/${reading.id}/discard`), "Discarded")) onDiscard();
  };
  const eq = [res.equipment.manufacturer, res.equipment.model, res.equipment.engine && `engine ${res.equipment.engine}`].filter(Boolean).join(" ");
  return (
    <Sheet title="Check what the manual says" onClose={onClose}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          {eq ? <>Read as <b>{eq}</b>. </> : null}Untick anything you don't want, and correct anything that's wrong. Page numbers point to the manual.
          Part numbers are only ever copied from the manual, never guessed.
        </p>

        {(res.notes || []).map((n) => <div key={n} className="notice">{n}</div>)}
        <h3 style={{ margin: "6px 0 0" }}>Services</h3>
        {services.map((s, i) => (
          <div key={i} className={`notice stack ${s.include ? "" : "muted"}`} style={{ gap: 6 }}>
            <label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={s.include} onChange={(e) => setS(i, { include: e.target.checked })} />
              <input className="grow" value={s.title} onChange={(e) => setS(i, { title: e.target.value })} style={{ fontWeight: 600 }} />
            </label>
            {s.include && (
              <>
                <div className="s">{interval(s)}{s.page ? ` · page ${s.page}` : ""}</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 6 }}>
                  <label className="field">Every … h<input type="number" inputMode="decimal" min={0} value={s.interval_hours ?? ""} onChange={(e) => setS(i, { interval_hours: e.target.value ? Number(e.target.value) : null })} /></label>
                  <label className="field">or … days<input type="number" inputMode="numeric" min={0} value={s.interval_days ?? ""} onChange={(e) => setS(i, { interval_days: e.target.value ? Number(e.target.value) : null })} /></label>
                  <label className="field">First at … h<input type="number" inputMode="decimal" min={0} value={s.first_at_hours ?? ""} onChange={(e) => setS(i, { first_at_hours: e.target.value ? Number(e.target.value) : null })} /></label>
                </div>
                {s.task_match && (
                  <div>
                    <label className="field">Task
                      <select value={s.task_id ?? ""} onChange={(e) => setS(i, { task_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value={s.task_match.id}>Update “{s.task_match.title}”</option>
                        <option value="">Add as a new task</option>
                      </select>
                    </label>
                  </div>
                )}
                <div className="s">
                  {s.needs.length ? <>Needs: {s.needs.map((n) => `${amount(n.quantity, n.unit)} ${byKey[n.key]?.name || n.key}`).join(", ")}</> : "Needs no parts"}
                </div>
              </>
            )}
          </div>
        ))}

        <h3 style={{ margin: "6px 0 0" }}>Parts and consumables</h3>
        {parts.map((p, i) => (
          <div key={p.key} className={`notice stack ${p.include ? "" : "muted"}`} style={{ gap: 6 }}>
            <label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={p.include} onChange={(e) => setP(i, { include: e.target.checked })} />
              <b className="grow">{p.role}{p.quantity ? ` · ${amount(p.quantity, p.unit)}` : ""}</b>
              {p.page ? <span className="muted">p. {p.page}</span> : null}
            </label>
            {p.include && (
              <>
                <div className="s">
                  {[p.manufacturer, p.part_number && <span key="n" className="mono">{p.part_number}</span>, p.specification].filter(Boolean).map((x, k) => <span key={k}>{k ? " · " : ""}{x}</span>)}
                  {p.alternatives.length ? <> · also {p.alternatives.join(", ")}</> : null}
                </div>
                {p.match && (
                  <label className="row" style={{ gap: 8 }}>
                    <input type="checkbox" checked={p.use_match} onChange={(e) => setP(i, { use_match: e.target.checked })} />
                    <span>Use <b>{partName(p.match)}</b> {p.match.is_generic ? "(your standard)" : "(already in your inventory)"}</span>
                  </label>
                )}
                {!(p.match && p.use_match) && (
                  <>
                    <label className="field">Name<input value={p.name || ""} onChange={(e) => setP(i, { name: e.target.value })} /></label>
                    <label className="row" style={{ gap: 8 }}>
                      <input type="checkbox" checked={p.is_generic} onChange={(e) => setP(i, { is_generic: e.target.checked })} />
                      <span>Any brand that meets the specification (a standard)</span>
                    </label>
                    {p.is_generic && (
                      <label className="field">Match brands by<input value={p.spec_value} onChange={(e) => setP(i, { spec_value: e.target.value })} placeholder="10W-30" />
                        <span className="hint">Products with this in their specifications are suggested as meeting it; you confirm each one.</span></label>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        ))}

        {specs.length > 0 && (
          <>
            <h3 style={{ margin: "6px 0 0" }}>Other specifications</h3>
            <ul className="list">
              {specs.map((s, i) => (
                <li key={i}><label className="row grow" style={{ gap: 8 }}>
                  <input type="checkbox" checked={s.include} onChange={(e) => setSpecs(specs.map((x, j) => (j === i ? { ...x, include: e.target.checked } : x)))} />
                  <span className="grow"><b>{s.label}</b>: {s.value}{s.page ? <span className="muted"> · p. {s.page}</span> : null}</span>
                </label></li>
              ))}
            </ul>
          </>
        )}
        <button className="btn primary block" disabled={busy} onClick={apply}><Check size={18} /> Save the plan</button>
        <button className="btn block" disabled={busy} onClick={discard}><X size={18} /> Discard</button>
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------- what the model takes

export function ModelParts({ data }: { data: ItemParts }) {
  const rows = data.fitments.filter((f) => f.status !== "rejected");
  if (!rows.length) return null;
  return (
    <>
      <h3 style={{ margin: "16px 0 6px" }}>Parts this model takes</h3>
      <ul className="list">
        {rows.map((f) => (
          <li key={f.id}>
            <div className="grow">
              <div className="t">{f.role ? <>{f.role}: </> : null}{partName(f.part)} {f.part.is_generic && <span className="badge blue">Any brand</span>}</div>
              <div className="s">{[amount(f.quantity, f.unit), f.source_ref].filter(Boolean).join(" · ")}</div>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

// ---------------------------------------------------------------- readiness across equipment

export function ReadinessPanel() {
  const [within, setWithin] = useState(30);
  const d = useApi(() => api.get<ReadinessOverview>("parts/readiness", { within }), [within]);
  const { busy, run } = useAction();
  const toList = async (l: ReadinessLine) => {
    if (!l.buy) return;
    await run(() => api.post("shopping-list", { product_id: l.buy!.product_id, quantity: l.buy!.quantity, note: `For ${(l.tasks || []).map((t) => t.item.name).join(", ")}` }), "Added to the shopping list");
  };
  if (d.loading && !d.data) return <Spinner />;
  if (d.error || !d.data) return <ErrorBox error={d.error} />;
  const x = d.data;
  return (
    <>
      <div className="tabs">
        {[7, 30, 90].map((n) => <button key={n} className={within === n ? "active" : ""} onClick={() => setWithin(n)}>Next {n} days</button>)}
      </div>
      {x.lines.length ? (
        <ul className="list">
          {x.lines.map((l) => (
            <li key={l.key}>
              <div className="grow">
                <div className="t">{amount(l.quantity, l.unit)} {l.product.name} <ReadyBadge status={l.status} /></div>
                <div className="s">{why(l)}{l.sources.length ? ` · from ${l.sources.filter((s) => s.sure).map((s) => s.product.name).join(", ") || "unconfirmed equivalents"}` : ""}</div>
                <div className="s">For {(l.tasks || []).map((t, i) => <span key={t.id}>{i ? ", " : ""}<Link to={`/items/${t.item.id}`}>{t.item.name}</Link> – {t.title}</span>)}</div>
              </div>
              {l.buy && <button className="btn small" disabled={busy} onClick={() => toList(l)} aria-label={`Add ${l.buy.name} to the shopping list`}><ShoppingCart size={16} /> {l.buy.quantity}</button>}
            </li>
          ))}
        </ul>
      ) : <div className="empty">No parts needed for services due in the next {within} days.</div>}
      {x.tasks_without_parts.length > 0 && (
        <p className="muted">
          Due without parts listed: {x.tasks_without_parts.map((t, i) => <span key={t.id}>{i ? ", " : ""}<Link to={`/items/${t.item.id}`}>{t.item.name}</Link> – {t.title}</span>)}.
          Add parts on the item, or read its manual.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------- part details on a product

export function PartDetailsSection({ productId, edit, open, setOpen }: { productId: number; edit: boolean; open: boolean; setOpen: (v: boolean) => void }) {
  const d = useApi(() => api.get<PartDetails>(`products/${productId}/parts`), [productId]);
  if (d.loading || !d.data) return null;
  const p = d.data;
  const links = p.links.filter((l) => l.status !== "rejected");
  const meeting = p.linked_from.filter((l) => l.kind === "meets" && l.status !== "rejected");
  const has = p.is_generic || Object.keys(p.specs).length || p.content_quantity || links.length || meeting.length || p.fits.length;
  if (!has && !open) return null;
  const rows: [string, ReactNode][] = [];
  if (p.content_quantity) rows.push(["Pack size", amount(p.content_quantity, p.content_unit)]);
  for (const [k, v] of Object.entries(p.specs)) rows.push([k, v]);
  return (
    <Section title={p.is_generic ? "Standard" : "Part / consumable"} action={edit && <button className="btn small" onClick={() => setOpen(true)}>Edit</button>}>
      {!has && <p className="muted" style={{ margin: 0 }}>Record the pack size and specifications so equipment that needs this can use this stock.</p>}
      {p.is_generic && <p className="muted" style={{ marginTop: 0 }}>A standard, not a brand: anything confirmed as meeting it counts as stock for it.</p>}
      {rows.length > 0 && <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>}
      {links.map((l) => <LinkRow key={l.id} l={l} edit={edit} onChanged={d.reload} text={l.kind === "meets" ? <>Meets <b>{l.target.name}</b></> : <>Replaced by <b>{partName(l.target)}</b></>} />)}
      {meeting.length > 0 && <h3 style={{ margin: "12px 0 4px" }}>Brands that meet it</h3>}
      {meeting.map((l) => <LinkRow key={l.id} l={l} edit={edit} onChanged={d.reload} text={<b>{partName(l.product)}</b>} />)}
      {p.fits.length > 0 && (
        <p className="s" style={{ marginBottom: 0 }}>Used by: {p.fits.map((f) => f.equipment.name).join(", ")}</p>
      )}
      {open && <PartDetailsSheet p={p} onClose={() => setOpen(false)} onDone={() => { setOpen(false); d.reload(); }} />}
    </Section>
  );
}

function LinkRow({ l, edit, text, onChanged }: { l: { id: number; status: string; note: string | null }; edit: boolean; text: ReactNode; onChanged: () => void }) {
  const { busy, run } = useAction();
  const set = async (status: string) => { if (await run(() => api.patch(`product-links/${l.id}`, { status }))) onChanged(); };
  return (
    <div className="row between wrap" style={{ padding: "6px 0", gap: 6 }}>
      <span>{text} {l.status === "suggested" ? <span className="badge warn">Suggested{l.note ? ` – ${l.note.toLowerCase()}` : ""}</span> : <span className="badge ok">Confirmed</span>}</span>
      {edit && l.status === "suggested" && (
        <span className="row" style={{ gap: 6 }}>
          <button className="btn small primary" disabled={busy} onClick={() => set("confirmed")}><Check size={16} /> Confirm</button>
          <button className="btn small" disabled={busy} onClick={() => set("rejected")}>Not equivalent</button>
        </span>
      )}
      {edit && l.status === "confirmed" && <button className="btn small ghost" disabled={busy} onClick={() => set("rejected")}>Remove</button>}
    </div>
  );
}

function PartDetailsSheet({ p, onClose, onDone }: { p: PartDetails; onClose: () => void; onDone: () => void }) {
  const [generic, setGeneric] = useState(p.is_generic);
  const [specs, setSpecs] = useState<[string, string][]>(Object.entries(p.specs).length ? Object.entries(p.specs) : [["", ""]]);
  const [qty, setQty] = useState(p.content_quantity ? String(p.content_quantity) : "");
  const [unit, setUnit] = useState(p.content_unit || "");
  const [link, setLink] = useState<null | "meets" | "replaced_by">(null);
  const { busy, run } = useAction();
  const save = async () => {
    const body = { is_generic: generic, specs: Object.fromEntries(specs.filter(([k, v]) => k.trim() && v.trim())), content_quantity: qty ? Number(qty) : null, content_unit: unit || null };
    if (await run(() => api.put(`products/${p.id}/parts`, body), "Saved")) onDone();
  };
  const addLink = async (target: PartBrief) => {
    if (await run(() => api.post(`products/${p.id}/links`, { target_id: target.id, kind: link, status: "confirmed" }), "Linked")) onDone();
  };
  return (
    <Sheet title="Part details" onClose={onClose}>
      <div className="stack">
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={generic} onChange={(e) => setGeneric(e.target.checked)} />
          <span>This is a standard, not a brand <span className="muted">(e.g. “Engine oil 10W-30”: any brand that meets it will do)</span></span></label>
        {!generic && (
          <div className="grid two">
            <label className="field">One pack holds<input type="number" inputMode="decimal" step="0.001" min={0} value={qty} onChange={(e) => setQty(e.target.value)} placeholder="5" /></label>
            <label className="field">Unit<input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="L, ml, kg, g" /></label>
          </div>
        )}
        <b>Specifications</b>
        <span className="hint" style={{ marginTop: -8 }}>Matching specifications suggest which standards a product meets, for example Viscosity: 10W-30.</span>
        {specs.map(([k, v], i) => (
          <div key={i} className="row" style={{ gap: 6 }}>
            <input value={k} onChange={(e) => setSpecs(specs.map((x, j) => (j === i ? [e.target.value, x[1]] : x)))} placeholder="Viscosity" style={{ flex: 1 }} />
            <input value={v} onChange={(e) => setSpecs(specs.map((x, j) => (j === i ? [x[0], e.target.value] : x)))} placeholder="10W-30" style={{ flex: 1 }} />
            <button className="iconbtn" aria-label="Remove" onClick={() => setSpecs(specs.filter((_, j) => j !== i))}><Trash2 size={18} /></button>
          </div>
        ))}
        <button className="btn small" onClick={() => setSpecs([...specs, ["", ""]])}><Plus size={16} /> Add specification</button>
        <button className="btn primary block" disabled={busy} onClick={save}>Save</button>
        {!p.is_generic && (
          <>
            <div className="row wrap" style={{ gap: 6 }}>
              <button className="btn small" onClick={() => setLink("meets")}>It meets a standard…</button>
              <button className="btn small" onClick={() => setLink("replaced_by")}>It's been replaced by…</button>
            </div>
            {link && <PartPicker onPick={addLink} />}
          </>
        )}
      </div>
    </Sheet>
  );
}
