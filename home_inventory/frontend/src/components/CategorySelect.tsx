// Categories are a fixed list (Admin → Categories); items choose from it.
import { useEffect, useState } from "react";
import { api } from "../api";
import { useMe } from "./ui";

export interface Category { id: number; name: string; synonyms: string | null; item_count: number }

let cache: Promise<Category[]> | null = null;
const listeners = new Set<(c: Category[]) => void>();

/** The category list, loaded once and shared; ``refreshCategories`` reloads it. */
export function useCategories(): Category[] | null {
  const [cats, setCats] = useState<Category[] | null>(null);
  useEffect(() => {
    if (!cache) cache = api.get<Category[]>("categories").catch((e) => { cache = null; throw e; });
    cache.then(setCats).catch(() => setCats([]));
    listeners.add(setCats);
    return () => { listeners.delete(setCats); };
  }, []);
  return cats;
}

export function refreshCategories() {
  cache = api.get<Category[]>("categories");
  cache.then((c) => listeners.forEach((l) => l(c))).catch(() => undefined);
}

/** Same rules as the App: "Kitchen & Dining" = "kitchen and dining", "Tools" = "tool". */
export function categoryKey(name: string | null | undefined): string {
  return (name || "").toLowerCase().replace(/&/g, " and ").match(/[a-z0-9]+/g)?.map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w)).join(" ") || "";
}

/** The category a suggested name (AI, barcode, an older record) means, if any. */
export function matchCategory(name: string | null | undefined, cats: Category[]): Category | null {
  if (!name?.trim()) return null;
  const k = categoryKey(name);
  return cats.find((c) => c.name.toLowerCase() === name.trim().toLowerCase())
    || cats.find((c) => categoryKey(c.name) === k)
    || cats.find((c) => (c.synonyms || "").split(",").some((s) => s.trim() && categoryKey(s) === k))
    || (k === "equipment" ? { id: 0, name: "Equipment", synonyms: null, item_count: 0 } : null);
}

/**
 * Choose a category. A value that isn't one of the categories (an AI or
 * barcode suggestion) is matched to one if it can be, otherwise cleared and
 * shown as a hint. Administrators can add a new category from here.
 */
export function CategorySelect({ value, onChange, emptyLabel = "No category", id }: {
  value: string; onChange: (v: string) => void; emptyLabel?: string; id?: string;
}) {
  const cats = useCategories();
  const me = useMe();
  const [suggested, setSuggested] = useState<string | null>(null);
  useEffect(() => {
    if (!cats || !value) return;
    if (cats.some((c) => c.name === value)) return;
    const m = matchCategory(value, cats);
    if (m) onChange(m.name);
    else { setSuggested(value); onChange(""); }
  }, [cats, value]); // eslint-disable-line react-hooks/exhaustive-deps
  const options = [...(cats || [])];
  if (value && !options.some((c) => c.name === value)) options.push({ id: -1, name: value, synonyms: null, item_count: 0 });

  async function addNew() {
    const name = window.prompt("New category name (it becomes one of the choices for every item):", suggested || "");
    if (!name?.trim()) return;
    const near = cats && matchCategory(name, cats);
    if (near) { onChange(near.name); return; }
    try {
      const c = await api.post<Category>("categories", { name: name.trim() });
      refreshCategories();
      onChange(c.name);
      setSuggested(null);
    } catch (e) {
      window.alert((e as Error).message);
    }
  }

  return (
    <>
      <select id={id} value={value} onChange={(e) => (e.target.value === "__new" ? void addNew() : onChange(e.target.value))}>
        <option value="">{emptyLabel}</option>
        {options.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        {me.is_admin && <option value="__new">＋ New category…</option>}
      </select>
      {suggested && !value && <small className="muted">Suggested “{suggested}” isn’t one of your categories{me.is_admin ? "; choose one, or add it with ＋ New category" : "; choose the closest"}.</small>}
    </>
  );
}
