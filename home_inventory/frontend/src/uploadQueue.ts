/**
 * Quick-photo upload queue that survives closing the app.
 *
 * Each photo is saved on the phone (IndexedDB) before it is uploaded and
 * removed only once the server has it.  Uploads run one at a time; if the
 * network drops, the queue waits and retries (on "online", every 20 s, and
 * whenever the app is opened again).  If IndexedDB isn't available (some
 * private-browsing modes) the queue still works, in memory only.
 */
import { ApiError, api, type Item } from "./api";

export interface Queued {
  id: string;
  blob: Blob;
  fields: Record<string, string>;
  created: number;
  error?: string; // set when the server refused it (won't retry by itself)
}

export type QueueEvent =
  | { type: "added"; entry: Queued }
  | { type: "uploading"; id: string }
  | { type: "done"; id: string; item: Item }
  | { type: "failed"; id: string; error: string; retrying: boolean }
  | { type: "removed"; id: string };

const DB = "home-inventory";
const STORE = "uploads";
const memory = new Map<string, Queued>();
const listeners = new Set<(e: QueueEvent) => void>();
let busy = false;
let timer = 0;

function openDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T | null> {
  const db = await openDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const req = fn(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

const emit = (e: QueueEvent) => listeners.forEach((l) => l(e));

export function subscribe(fn: (e: QueueEvent) => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Everything still waiting (oldest first), including refused ones. */
export async function pending(): Promise<Queued[]> {
  const stored = (await tx<Queued[]>("readonly", (s) => s.getAll())) || [];
  const all = new Map<string, Queued>(stored.map((q) => [q.id, q]));
  memory.forEach((q, id) => all.set(id, q));
  return [...all.values()].sort((a, b) => a.created - b.created);
}

export async function enqueue(blob: Blob, fields: Record<string, string>): Promise<Queued> {
  const entry: Queued = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, blob, fields, created: Date.now() };
  const saved = await tx("readwrite", (s) => s.put(entry));
  if (saved === null) memory.set(entry.id, entry);
  emit({ type: "added", entry });
  void pump();
  return entry;
}

async function remove(id: string) {
  memory.delete(id);
  await tx("readwrite", (s) => s.delete(id));
}

async function save(entry: Queued) {
  if (memory.has(entry.id)) memory.set(entry.id, entry);
  else await tx("readwrite", (s) => s.put(entry));
}

/** Try a refused upload again. */
export async function retry(id: string) {
  const q = (await pending()).find((x) => x.id === id);
  if (!q) return;
  delete q.error;
  await save(q);
  void pump();
}

/** Give up on an upload (the photo is dropped). */
export async function discard(id: string) {
  await remove(id);
  emit({ type: "removed", id });
}

/** Worth retrying later: no connection, the App restarting, or a lapsed
 * Home Assistant session (renewed when Home Assistant is opened again). */
function isTemporary(e: unknown) {
  if (e instanceof ApiError) return [401, 408, 429, 500, 502, 503, 504].includes(e.status);
  return e instanceof TypeError || !navigator.onLine;
}

/** Upload everything waiting, one at a time. */
export async function pump(): Promise<void> {
  if (busy) return;
  busy = true;
  try {
    for (const q of await pending()) {
      if (q.error) continue;
      emit({ type: "uploading", id: q.id });
      const fd = new FormData();
      fd.append("file", q.blob, "photo.jpg");
      Object.entries(q.fields).forEach(([k, v]) => fd.append(k, v));
      try {
        const item = await api.upload<Item>("capture", fd);
        await remove(q.id);
        emit({ type: "done", id: q.id, item });
      } catch (e) {
        const msg = (e as Error).message || "Upload failed";
        if (isTemporary(e)) {
          emit({ type: "failed", id: q.id, error: "Waiting for connection…", retrying: true });
          schedule();
          return; // keep order; try again later
        }
        q.error = msg;
        await save(q);
        emit({ type: "failed", id: q.id, error: msg, retrying: false });
      }
    }
  } finally {
    busy = false;
  }
}

function schedule() {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void pump(), 20000);
}

let started = false;
/** Call once at app start: resume anything left from before. */
export function startQueue() {
  if (started) return;
  started = true;
  window.addEventListener("online", () => void pump());
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void pump(); });
  void pump();
}

/** How many photos are still waiting, kept up to date. */
export async function waitingCount(): Promise<number> {
  return (await pending()).length;
}
