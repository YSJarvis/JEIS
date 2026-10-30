/**
 * Minimal promise wrapper over IndexedDB. Falls back to an in-memory store
 * when IndexedDB is unavailable (some private-browsing modes), so the app
 * still works for the session — it just won't survive a reload.
 */

const DB_NAME = "voterengine-offline";
const VERSION = 2;
export const STORES = { routes: "routes", outbox: "outbox", points: "points" } as const;
type StoreName = (typeof STORES)[keyof typeof STORES];

let dbPromise: Promise<IDBDatabase | null> | null = null;
const memory: Record<StoreName, Map<IDBValidKey, unknown>> = { routes: new Map(), outbox: new Map(), points: new Map() };

function open(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") return resolve(null);
    try {
      const r = indexedDB.open(DB_NAME, VERSION);
      r.onupgradeneeded = () => {
        const db = r.result;
        if (!db.objectStoreNames.contains(STORES.routes)) db.createObjectStore(STORES.routes, { keyPath: "key" });
        if (!db.objectStoreNames.contains(STORES.points)) db.createObjectStore(STORES.points, { keyPath: "key" });
        if (!db.objectStoreNames.contains(STORES.outbox)) {
          const s = db.createObjectStore(STORES.outbox, { keyPath: "op_id" });
          s.createIndex("cadreId", "cadreId");
        }
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => resolve(null);
      r.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

function done<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
}

const keyOf = (store: StoreName, v: Record<string, unknown>) => (store === "outbox" ? v.op_id : v.key) as IDBValidKey;

export async function put<T extends object>(store: StoreName, value: T): Promise<void> {
  const db = await open();
  if (!db) { memory[store].set(keyOf(store, value as Record<string, unknown>), value); return; }
  await done(db.transaction(store, "readwrite").objectStore(store).put(value));
}

export async function get<T>(store: StoreName, key: IDBValidKey): Promise<T | undefined> {
  const db = await open();
  if (!db) return memory[store].get(key) as T | undefined;
  return done(db.transaction(store).objectStore(store).get(key)) as Promise<T | undefined>;
}

export async function all<T>(store: StoreName, index?: { name: string; value: IDBValidKey }): Promise<T[]> {
  const db = await open();
  if (!db) {
    const vals = [...memory[store].values()] as T[];
    return index ? vals.filter((v) => (v as Record<string, unknown>)[index.name] === index.value) : vals;
  }
  const os = db.transaction(store).objectStore(store);
  return done(index ? os.index(index.name).getAll(index.value) : os.getAll()) as Promise<T[]>;
}

export async function del(store: StoreName, keys: IDBValidKey[]): Promise<void> {
  if (!keys.length) return;
  const db = await open();
  if (!db) { keys.forEach((k) => memory[store].delete(k)); return; }
  const tx = db.transaction(store, "readwrite");
  keys.forEach((k) => tx.objectStore(store).delete(k));
  await new Promise<void>((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); });
}

export async function destroy(): Promise<void> {
  const db = await open();
  memory.routes.clear(); memory.outbox.clear(); memory.points.clear();
  if (!db) return;
  db.close();
  dbPromise = null;
  await new Promise<void>((res) => {
    const r = indexedDB.deleteDatabase(DB_NAME);
    r.onsuccess = r.onerror = r.onblocked = () => res();
  });
}
