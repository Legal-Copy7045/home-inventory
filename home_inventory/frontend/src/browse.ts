// Remembers the list the user last browsed, so an item page can step to
// the next or previous item in that same order.
import { api, type Query } from "./api";

type Ctx = { ids: number[]; label?: string } | { filters: Query; label?: string };
const KEY = "hi.browse";

export function rememberList(ctx: Ctx): void {
  try { sessionStorage.setItem(KEY, JSON.stringify(ctx)); } catch { /* private mode: fall back to name order */ }
}

function recalled(): Ctx {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as Ctx;
  } catch { /* ignore */ }
  return { filters: { sort: "name" } };
}

export async function neighbours(id: number): Promise<{ prev: number | null; next: number | null; pos: number; total: number }> {
  const ctx = recalled();
  let ids = "ids" in ctx ? ctx.ids : (await api.get<{ ids: number[] }>("items/ids", ctx.filters)).ids;
  let i = ids.indexOf(id);
  if (i < 0) {
    // Not in the remembered list (opened from elsewhere): use the whole inventory by name.
    ids = (await api.get<{ ids: number[] }>("items/ids", { sort: "name" })).ids;
    i = ids.indexOf(id);
  }
  if (i < 0) return { prev: null, next: null, pos: 0, total: ids.length };
  return { prev: i > 0 ? ids[i - 1] : null, next: i < ids.length - 1 ? ids[i + 1] : null, pos: i + 1, total: ids.length };
}
