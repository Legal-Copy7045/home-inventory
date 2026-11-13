import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { api } from "../api";
import { Section, money, useAction, useApi, useMe, usePoll } from "./ui";

interface Job {
  status: "pending" | "running" | "finished" | "cancelled" | "failed";
  scope: "unpriced" | "category" | "all";
  category: string | null;
  total: number;
  done: number;
  failed: number;
  estimated_total: number;
  currency: string | null;
  error: string | null;
  started_by: string | null;
}
interface Preview { items: number; requests: number; too_many: boolean; max: number; minutes: number | null; days: number | null }

const SCOPE_LABEL = { unpriced: "items without a price", category: "a category", all: "the whole inventory" };

/** "Estimate values with AI" for many items at once: what to include, a
 *  preview of how many AI requests that is, then progress. */
export function ValueEstimates({ onFinished }: { onFinished: () => void }) {
  const me = useMe();
  const [scope, setScope] = useState<Job["scope"]>("unpriced");
  const [categoryId, setCategoryId] = useState<number | "">("");
  const [redo, setRedo] = useState(false);
  const cats = useApi(() => api.get<{ id: number; name: string }[]>("categories"), []);
  const ready = scope !== "category" || categoryId !== "";
  const preview = useApi(
    () => (ready ? api.get<Preview>("value-estimates/preview", { scope, category_id: categoryId || undefined, redo_recent: redo }) : Promise.resolve(null)),
    [scope, categoryId, redo],
  );
  const state = useApi(() => api.get<{ job: Job | null }>("value-estimates"), []);
  const job = state.data?.job || null;
  const active = job?.status === "pending" || job?.status === "running";
  const { busy, run } = useAction();
  usePoll(active, state.reload, 2000);
  const [wasActive, setWasActive] = useState(false);
  useEffect(() => {
    if (active) setWasActive(true);
    else if (wasActive) { setWasActive(false); preview.reload(); onFinished(); }
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!me.app.ai.enabled) return null;

  const start = async () => {
    if (!preview.data) return;
    const what = scope === "category" ? cats.data?.find((c) => c.id === categoryId)?.name : SCOPE_LABEL[scope];
    if (!window.confirm(`Estimate the value of ${preview.data.items} item${preview.data.items === 1 ? "" : "s"} (${what}) with AI? That's about ${preview.data.requests} AI request${preview.data.requests === 1 ? "" : "s"}, which may cost money with a paid AI provider.`)) return;
    if (await run(() => api.post("value-estimates", { scope, category_id: scope === "category" ? categoryId : null, redo_recent: redo }))) state.reload();
  };
  const pct = job && job.total ? Math.round(((job.done + job.failed) / job.total) * 100) : 0;

  return (
    <Section title="Estimate values with AI">
      {active && job ? (
        <div className="stack">
          <div><b>Estimating {job.category || SCOPE_LABEL[job.scope]}…</b> {job.done + job.failed} of {job.total}</div>
          <div style={{ background: "var(--surface-2)", borderRadius: 6, height: 10, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: "var(--primary)", transition: "width .3s" }} />
          </div>
          <p className="muted" style={{ margin: 0 }}>You can leave this page; it carries on in the background.</p>
          {me.is_admin && <button className="btn small ghost" disabled={busy} onClick={() => run(() => api.post("value-estimates/cancel")).then(state.reload)}>Stop</button>}
        </div>
      ) : (
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>
            AI suggests what each item would cost to replace new, and its second-hand value, from its name, make, model, age and photo. Estimates are kept with each item, marked as AI and dated. A price you entered always comes first.
          </p>
          {job && job.status !== "pending" && job.status !== "running" && (
            <div className={job.status === "finished" ? "notice" : "error"}>
              {job.status === "finished" && <>Estimated {job.done} item{job.done === 1 ? "" : "s"}{job.estimated_total ? `: ≈ ${money(job.estimated_total, job.currency || me.app.currency)} to replace` : ""}.{job.failed ? ` ${job.failed} couldn't be estimated.` : ""}</>}
              {job.status === "cancelled" && <>Stopped after {job.done} item{job.done === 1 ? "" : "s"}.</>}
              {job.status === "failed" && <>Stopped: {job.error}</>}
            </div>
          )}
          {me.is_admin ? (
            <>
              <div className="stack" style={{ gap: 6 }}>
                {(["unpriced", "category", "all"] as const).map((s) => (
                  <label key={s} className="check">
                    <input type="radio" name="value-scope" checked={scope === s} onChange={() => setScope(s)} />
                    <span>{s === "unpriced" ? "Items without a price" : s === "category" ? "Items in a category" : "Everything"}</span>
                  </label>
                ))}
                {scope === "category" && (
                  <select value={categoryId} onChange={(e) => setCategoryId(e.target.value ? Number(e.target.value) : "")} aria-label="Category to estimate" style={{ width: "auto" }}>
                    <option value="">Choose a category…</option>
                    {cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                )}
                <label className="check">
                  <input type="checkbox" checked={redo} onChange={(e) => setRedo(e.target.checked)} />
                  <span>Include items estimated in the last 30 days</span>
                </label>
              </div>
              {preview.data && (
                <p className="muted" style={{ margin: 0 }}>
                  {preview.data.items ? `${preview.data.items} item${preview.data.items === 1 ? "" : "s"}, ${preview.data.requests} AI request${preview.data.requests === 1 ? "" : "s"} (the same product in several places is estimated once).` : "Nothing to estimate: these items all have a recent estimate."}
                  {preview.data.items && preview.data.days && preview.data.days > 1 ? ` Within your AI limits that takes about ${preview.data.days} days; it carries on in the background.` : preview.data.items && preview.data.minutes && preview.data.minutes > 1 ? ` Within your AI limits that takes about ${preview.data.minutes} minutes.` : ""}
                  {preview.data.too_many ? ` That's more than ${preview.data.max} at once; choose a category.` : ""}
                </p>
              )}
              <button className="btn primary" disabled={busy || !ready || !preview.data?.items || preview.data.too_many} onClick={start}>
                <Sparkles size={18} /> Estimate values
              </button>
            </>
          ) : <p className="muted" style={{ margin: 0 }}>An administrator can estimate values for many items at once.</p>}
        </div>
      )}
    </Section>
  );
}
