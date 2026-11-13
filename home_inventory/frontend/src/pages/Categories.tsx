// Admin → Categories: the fixed list items choose from.
import { useState } from "react";
import { Link } from "react-router-dom";
import { GitMerge, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { categoryKey, refreshCategories, type Category } from "../components/CategorySelect";
import { ErrorBox, Sheet, Spinner, useAction, useApi, useMe } from "../components/ui";

const SYSTEM = new Set(["equipment"]);

export default function CategoriesPage() {
  const me = useMe();
  const list = useApi(() => api.get<Category[]>("categories"), []);
  const { busy, run } = useAction();
  const [adding, setAdding] = useState("");
  const [editing, setEditing] = useState<Category | null>(null);
  const [merging, setMerging] = useState<Category | null>(null);
  const reload = () => { list.reload(); refreshCategories(); };

  if (!list.data) return list.error ? <div className="page"><ErrorBox error={list.error} /></div> : <Spinner />;
  const cats = list.data;
  const admin = me.is_admin;
  const similar = (c: Category) => cats.filter((o) => o.id !== c.id && (categoryKey(o.name).split(" ").some((w) => w.length > 3 && categoryKey(c.name).includes(w))));

  return (
    <div className="page">
      <h1>Categories</h1>
      <p className="muted">Items choose their category from this list. {admin ? "Merge two that mean the same (\"Kitchenware\" into \"Kitchen & Dining\") to move their items across; the old name is kept as a synonym, so AI and barcode suggestions with it land in the right place." : "Only an administrator can change it."}</p>
      {admin && (
        <form className="row" style={{ gap: 8, marginBottom: 12 }} onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api.post("categories", { name: adding.trim() }), `Added ${adding.trim()}`)) { setAdding(""); reload(); }
        }}>
          <input className="grow" value={adding} onChange={(e) => setAdding(e.target.value)} placeholder="New category, e.g. Kitchen & Dining" maxLength={120} />
          <button className="btn primary" disabled={busy || !adding.trim()}><Plus size={18} /> Add</button>
        </form>
      )}
      <div className="list card tight">
        {cats.map((c) => (
          <li key={c.id}>
            <div className="grow">
              <div className="t"><Link to={`/items?category=${encodeURIComponent(c.name)}`}>{c.name}</Link></div>
              <div className="s">{c.item_count} item{c.item_count === 1 ? "" : "s"}{c.synonyms ? ` · also: ${c.synonyms}` : ""}
                {admin && similar(c).length > 0 && <> · similar to {similar(c).map((o) => o.name).join(", ")}</>}</div>
            </div>
            {admin && (
              <span className="row" style={{ gap: 4 }}>
                <button className="iconbtn" aria-label={`Rename ${c.name}`} onClick={() => setEditing(c)}><Pencil size={18} /></button>
                {!SYSTEM.has(c.name.toLowerCase()) && <>
                  <button className="iconbtn" aria-label={`Merge ${c.name}`} onClick={() => setMerging(c)}><GitMerge size={18} /></button>
                  {c.item_count === 0 && <button className="iconbtn" aria-label={`Delete ${c.name}`} disabled={busy} onClick={async () => {
                    if (!window.confirm(`Delete the category ${c.name}?`)) return;
                    if (await run(() => api.del(`categories/${c.id}`), `Deleted ${c.name}`)) reload();
                  }}><Trash2 size={18} /></button>}
                </>}
              </span>
            )}
          </li>
        ))}
        {!cats.length && <div className="empty">No categories yet.</div>}
      </div>

      {editing && <EditSheet cat={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); reload(); }} />}
      {merging && (
        <Sheet title={`Merge ${merging.name} into…`} onClose={() => setMerging(null)}>
          <p className="muted" style={{ marginTop: 0 }}>Its {merging.item_count} item{merging.item_count === 1 ? "" : "s"} move to the category you choose, and {merging.name} becomes a synonym of it.</p>
          <div className="list card tight">
            {cats.filter((o) => o.id !== merging.id).map((o) => (
              <a key={o.id} href="#" onClick={async (e) => {
                e.preventDefault();
                const src = merging;
                setMerging(null);
                if (await run(() => api.post(`categories/${src.id}/merge`, { into_id: o.id }), `Merged ${src.name} into ${o.name}`)) reload();
              }}>
                <div className="grow"><div className="t">{o.name}</div><div className="s">{o.item_count} item{o.item_count === 1 ? "" : "s"}</div></div>
              </a>
            ))}
          </div>
        </Sheet>
      )}
    </div>
  );
}

function EditSheet({ cat, onClose, onDone }: { cat: Category; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(cat.name);
  const [syn, setSyn] = useState(cat.synonyms || "");
  const { busy, run } = useAction();
  return (
    <Sheet title={`Edit ${cat.name}`} onClose={onClose}>
      <div className="stack">
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} /></label>
        <label className="field">Also known as (comma-separated; used by search and to match AI suggestions)
          <input value={syn} onChange={(e) => setSyn(e.target.value)} placeholder="kitchenware, cookware" maxLength={2000} />
        </label>
        <button className="btn primary block" disabled={busy || !name.trim()} onClick={async () => {
          if (await run(() => api.patch(`categories/${cat.id}`, { name: name.trim(), synonyms: syn.trim() || null }), "Saved")) onDone();
        }}>Save</button>
      </div>
    </Sheet>
  );
}
