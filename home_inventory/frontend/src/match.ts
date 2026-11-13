/**
 * Forgiving word search: every word typed must match some word of the text,
 * in any order and anywhere in it (e.g. a location's whole path).
 *
 * "shed right front shelf" finds "Shed › Front Right Shelving › Right Bottom Shelf".
 * A typed word matches a word that starts with it ("sh" → "Shed"), or that
 * shares its stem: plural forms ("shelves" ↔ "shelf") and a different ending
 * after at least four shared letters ("shelf" ↔ "shelving"; "frog" ≠ "front").
 */
const words = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").split(/[^a-z0-9#]+/).filter(Boolean);

function stem(w: string): string {
  if (w.length > 4 && w.endsWith("ves")) return w.slice(0, -3) + "f";
  if (w.length > 4 && w.endsWith("ies")) return w.slice(0, -3) + "y";
  if (w.length > 4 && /(ss|x|ch|sh)es$/.test(w)) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !/(ss|us|is)$/.test(w)) return w.slice(0, -1);
  return w;
}

function common(a: string, b: string): number {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return i;
}

function wordMatches(q: string, t: string): boolean {
  if (t.startsWith(q)) return true;
  const sq = stem(q), st = stem(t);
  if (st.startsWith(sq) || sq === st) return true;
  const c = common(sq, st);
  return c >= 4 && c >= Math.min(sq.length, st.length) - 1;
}

export function matchesWords(query: string, text: string): boolean {
  const qs = words(query);
  if (!qs.length) return true;
  const ts = words(text);
  return qs.every((q) => ts.some((t) => wordMatches(q, t)));
}
