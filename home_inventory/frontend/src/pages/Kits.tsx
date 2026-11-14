import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Box, Link2, Package, Plus, Trash2, X } from "lucide-react";
import { TagsSection } from "./Tags";
import { api, type Item, type ItemRelations, type Kit, type KitMemberState } from "../api";
import { PlacementPicker, type PlacementValue } from "../components/PlacementPicker";
import { Empty, ErrorBox, Section, Sheet, Spinner, useAction, useApi, useMe } from "../components/ui";

const STATE_CLASS: Record<KitMemberState, string> = { present: "ok", elsewhere: "blue", on_loan: "warn", short: "warn", missing: "danger" };

/** Search the inventory and pick one item. */
export function ItemPickerSheet({ title, exclude = [], onPick, onClose }: { title: string; exclude?: number[]; onPick: (item: Item) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const res = useApi(() => api.get<{ items: Item[] }>("items", { q: q || undefined, limit: 40 }), [q]);
  return (
    <Sheet title={title} onClose={onClose}>
      <input type="search" autoFocus placeholder="Search your items" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%", marginBottom: 10 }} />
      {res.error && !res.data ? <ErrorBox error={res.error} retry={res.reload} /> : res.loading && !res.data ? <Spinner /> : (
        <div className="list">
          {(res.data?.items || []).filter((i) => !exclude.includes(i.id)).map((i) => (
            <button key={i.id} className="row" style={{ width: "100%", textAlign: "left", background: "none", border: 0, borderBottom: "1px solid var(--border)", padding: "12px 4px", font: "inherit", cursor: "pointer" }} onClick={() => onPick(i)}>
              <Package size={20} />
              <div className="grow"><div className="t">{i.display_name}</div><div className="s">{i.placement.path}</div></div>
            </button>
          ))}
        </div>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- item page section

/** ``hideWhenEmpty``: show nothing until something is linked (the item page
 *  offers "Related item" in its Add row instead); ``request`` opens the form. */
export function RelatedSection({ itemId, edit, hideWhenEmpty, request }: { itemId: number; edit: boolean; hideWhenEmpty?: boolean; request?: number }) {
  const r = useApi(() => api.get<ItemRelations>(`items/${itemId}/relations`), [itemId]);
  const [adding, setAdding] = useState(false);
  useEffect(() => { if (request) setAdding(true); }, [request]);
  const { run } = useAction();
  if (!r.data) return null;
  const { relations, kits } = r.data;
  if (!relations.length && !kits.length && (!edit || hideWhenEmpty)) {
    return adding ? <LinkSheet itemId={itemId} onClose={() => setAdding(false)} onDone={(d) => { r.setData(d); setAdding(false); }} /> : null;
  }
  const remove = async (id: number) => {
    if (!window.confirm("Unlink these items? Neither item is deleted.")) return;
    await run(() => api.del(`relations/${id}`), "Unlinked");
    r.reload();
  };
  const groups = relations.reduce<Record<string, typeof relations>>((acc, x) => ((acc[x.label] ||= []).push(x), acc), {});
  return (
    <Section title="Related items" action={edit && <button className="btn small" onClick={() => setAdding(true)}><Link2 size={16} /> Link</button>}>
      {kits.length > 0 && (
        <div className="row wrap" style={{ gap: 6, marginBottom: 10 }}>
          {kits.map((k) => <Link key={k.id} className="badge blue" to={`/kits/${k.id}`}><Box size={12} /> Kit: {k.name}</Link>)}
        </div>
      )}
      {Object.entries(groups).map(([label, rows]) => (
        <div key={label} style={{ marginBottom: 8 }}>
          <div className="muted" style={{ fontSize: ".85rem", fontWeight: 600 }}>{rows.length > 1 ? ({ Accessory: "Accessories", Component: "Components" } as Record<string, string>)[label] || label : label}</div>
          <ul className="list">
            {rows.map((x) => (
              <li key={x.id}>
                <Link className="grow" to={`/items/${x.item.id}`}>
                  <div className="t">{x.item.name}{x.item.label ? ` (${x.item.label})` : ""}</div>
                  <div className="s">{x.item.placement}{x.item.status !== "stored" ? ` · ${x.item.status.replace(/_/g, " ")}` : ""}</div>
                </Link>
                {edit && <button className="iconbtn" aria-label={`Unlink ${x.item.name}`} onClick={() => remove(x.id)}><X size={18} /></button>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {!relations.length && <p className="muted" style={{ margin: 0 }}>Link accessories, components, compatible parts or things stored together. Each item keeps its own place and QR code.</p>}
      {adding && <LinkSheet itemId={itemId} onClose={() => setAdding(false)} onDone={(d) => { r.setData(d); setAdding(false); }} />}
    </Section>
  );
}

const LINK_CHOICES: { key: string; type: string; direction: "this_to_other" | "other_to_this"; label: string }[] = [
  { key: "acc_of", type: "accessory_of", direction: "this_to_other", label: "This is an accessory of…" },
  { key: "has_acc", type: "accessory_of", direction: "other_to_this", label: "Add an accessory for this…" },
  { key: "comp_of", type: "component_of", direction: "this_to_other", label: "This is a component of…" },
  { key: "has_comp", type: "component_of", direction: "other_to_this", label: "Add a component of this…" },
  { key: "compat", type: "compatible_with", direction: "this_to_other", label: "Compatible with…" },
  { key: "stored", type: "stored_with", direction: "this_to_other", label: "Stored with…" },
  { key: "repl", type: "replacement_for", direction: "this_to_other", label: "This is a replacement for…" },
  { key: "custom", type: "custom", direction: "this_to_other", label: "Something else…" },
];

function LinkSheet({ itemId, onClose, onDone }: { itemId: number; onClose: () => void; onDone: (d: ItemRelations) => void }) {
  const [choice, setChoice] = useState(LINK_CHOICES[1]);
  const [custom, setCustom] = useState("");
  const [picking, setPicking] = useState(false);
  const { run } = useAction();
  const pick = async (other: Item) => {
    setPicking(false);
    const d = await run(() => api.post<ItemRelations>(`items/${itemId}/relations`, {
      other_item_id: other.id, relation_type: choice.type, direction: choice.direction, custom_label: choice.type === "custom" ? custom : null,
    }), "Linked");
    if (d) onDone(d);
  };
  return (
    <Sheet title="Link a related item" onClose={onClose}>
      <div className="stack">
        <div className="steps">
          {LINK_CHOICES.map((c) => <button key={c.key} type="button" className={choice.key === c.key ? "active" : ""} onClick={() => setChoice(c)}>{c.label}</button>)}
        </div>
        {choice.type === "custom" && <label className="field">Describe it<input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="e.g. charger for" /></label>}
        <button className="btn primary block" disabled={choice.type === "custom" && !custom.trim()} onClick={() => setPicking(true)}>Choose the item</button>
      </div>
      {picking && <ItemPickerSheet title={choice.label} exclude={[itemId]} onPick={pick} onClose={() => setPicking(false)} />}
    </Sheet>
  );
}

// ---------------------------------------------------------------- kits pages

export function KitsPage() {
  const me = useMe();
  const kits = useApi(() => api.get<Kit[]>("kits"), []);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  return (
    <div className="page">
      {me.can_edit && <div className="row" style={{ justifyContent: "flex-end", marginBottom: 10 }}><button className="btn primary" onClick={() => setAdding(true)}><Plus size={18} /> Kit</button></div>}
      <ErrorBox error={kits.error} />
      {kits.error && !kits.data ? <ErrorBox error={kits.error} retry={kits.reload} /> : kits.loading && !kits.data ? <Spinner /> : kits.data?.length ? (
        <div className="list">
          {kits.data.map((k) => (
            <Link key={k.id} to={`/kits/${k.id}`}>
              <Box size={20} />
              <div className="grow">
                <div className="t">{k.name}</div>
                <div className="s">{k.member_count} item{k.member_count === 1 ? "" : "s"}{k.home ? ` · kept in ${k.home}` : ""}</div>
              </div>
              {k.complete ? (k.counts.elsewhere ? <span className="badge blue">{k.counts.elsewhere} elsewhere</span> : <span className="badge ok">Complete</span>) : <span className="badge danger">Incomplete</span>}
            </Link>
          ))}
        </div>
      ) : <Empty>No kits yet. Group things that belong together, like a compressor and its hose, inflator and fittings.</Empty>}
      {adding && <KitSheet onClose={() => setAdding(false)} onDone={(k) => nav(`/kits/${k.id}`)} />}
    </div>
  );
}

function KitSheet({ kit, onClose, onDone }: { kit?: Kit; onClose: () => void; onDone: (k: Kit) => void }) {
  const [name, setName] = useState(kit?.name || "");
  const [description, setDescription] = useState(kit?.description || "");
  const [home, setHome] = useState<PlacementValue | null>(kit && kit.home ? { container_id: kit.container_id, location_id: kit.location_id, label: kit.home } : null);
  const [picking, setPicking] = useState(false);
  const { busy, run } = useAction();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body = { name, description: description || null, container_id: home?.container_id ?? null, location_id: home?.location_id ?? null };
    const k = await run(() => (kit ? api.patch<Kit>(`kits/${kit.id}`, body) : api.post<Kit>("kits", body)), "Saved");
    if (k) onDone(k);
  };
  return (
    <Sheet title={kit ? "Edit kit" : "New kit"} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <label className="field">Name<input required autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Air compressor kit" /></label>
        <label className="field">Description<textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="stack" style={{ gap: 6 }}>
          <b style={{ fontSize: ".9rem" }}>Kept in (optional)</b>
          <button type="button" className="btn" onClick={() => setPicking(true)}>{home ? home.label : "Choose a container or location"}</button>
          <span className="muted" style={{ fontSize: ".82rem" }}>Members can live elsewhere; the kit shows which are stored separately.</span>
        </div>
        {home && <button type="button" className="btn small" onClick={() => setHome(null)}>No fixed place</button>}
        <button className="btn primary block" disabled={busy}>Save</button>
      </form>
      {picking && <PlacementPicker title="Where is the kit kept?" onClose={() => setPicking(false)} onPick={(p) => { setHome(p); setPicking(false); }} />}
    </Sheet>
  );
}

export function KitPage() {
  const { id } = useParams();
  const me = useMe();
  const nav = useNavigate();
  const kit = useApi(() => api.get<Kit>(`kits/${id}`), [id]);
  const [sheet, setSheet] = useState<null | "edit" | "add">(null);
  const { run } = useAction();
  if (kit.error && !kit.data) return <ErrorBox error={kit.error} retry={kit.reload} />;
  if (kit.loading && !kit.data) return <Spinner />;
  if (kit.error || !kit.data) return <div className="page"><ErrorBox error={kit.error} /></div>;
  const k = kit.data;
  const edit = me.can_edit;
  const add = async (item: Item) => {
    setSheet(null);
    const x = await run(() => api.post<Kit>(`kits/${k.id}/members`, { item_id: item.id }), `Added ${item.name}`);
    if (x) kit.setData(x);
  };
  const removeMember = async (mid: number) => {
    await run(() => api.del(`kits/${k.id}/members/${mid}`), "Removed from kit");
    kit.reload();
  };
  const toggleOptional = async (mid: number, optional: boolean) => {
    const x = await run(() => api.patch<Kit>(`kits/${k.id}/members/${mid}`, { optional }));
    if (x) kit.setData(x);
  };
  const remove = async () => {
    if (!window.confirm(`Delete the kit "${k.name}"? Its items are not deleted.`)) return;
    await run(() => api.del(`kits/${k.id}`), "Kit deleted");
    nav("/kits");
  };
  return (
    <div className="page">
      <div className="row between wrap">
        <h1 style={{ margin: 0 }}>{k.name}</h1>
        {k.complete ? <span className="badge ok">All required items accounted for</span> : <span className="badge danger">Incomplete</span>}
      </div>
      <p className="muted" style={{ marginTop: 4 }}>{k.home ? `Kept in ${k.home}` : "No fixed place"}{k.description ? ` · ${k.description}` : ""}</p>
      {edit && (
        <div className="row wrap" style={{ marginBottom: 12 }}>
          <button className="btn" onClick={() => setSheet("add")}><Plus size={18} /> Add item</button>
          <button className="btn" onClick={() => setSheet("edit")}>Edit</button>
        </div>
      )}
      <Section title={`Items (${k.members?.length || 0})`} tight>
        {k.members?.length ? (
          <ul className="list">
            {k.members.map((m) => (
              <li key={m.id}>
                {m.item ? (
                  <Link className="grow" to={`/items/${m.item.id}`}>
                    <div className="t">{m.item.name}{m.quantity > 1 ? ` × ${m.quantity}` : ""}{m.optional ? <span className="muted"> (optional)</span> : null}</div>
                    <div className="s">{m.item.placement}</div>
                  </Link>
                ) : <div className="grow"><div className="t">Deleted item</div></div>}
                <span className={`badge ${STATE_CLASS[m.state]}`}>{m.state_label}</span>
                {edit && (
                  <>
                    <button className="btn small" onClick={() => toggleOptional(m.id, !m.optional)}>{m.optional ? "Make required" : "Optional"}</button>
                    <button className="iconbtn" aria-label="Remove from kit" onClick={() => removeMember(m.id)}><Trash2 size={18} /></button>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : <p className="muted" style={{ padding: "0 16px" }}>No items yet.</p>}
      </Section>
      <TagsSection targetType="kit" targetId={k.id} name={k.name} />
      {edit && <button className="btn block" onClick={remove}>Delete kit</button>}
      {sheet === "edit" && <KitSheet kit={k} onClose={() => setSheet(null)} onDone={(x) => { kit.setData(x); setSheet(null); }} />}
      {sheet === "add" && <ItemPickerSheet title="Add to kit" exclude={(k.members || []).map((m) => m.item_id)} onPick={add} onClose={() => setSheet(null)} />}
    </div>
  );
}
