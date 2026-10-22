// IndexedDB storage for the ledger. One database, six object stores, all of
// it inside this browser profile. Nothing here talks to a network.
import { STORES, keyOf } from "./ledger.js";

export const DB_NAME = "registrar";
const DB_VERSION = 1;

function req(r) {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

export function openDb(name = DB_NAME) {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(name, DB_VERSION);
    r.onupgradeneeded = () => {
      const db = r.result;
      for (const s of STORES) {
        if (db.objectStoreNames.contains(s)) continue;
        const auto = s === "evidence" || s === "fees";
        const os = db.createObjectStore(s, { keyPath: keyOf(s), autoIncrement: auto });
        if (s === "evidence") os.createIndex("entry_uuid", "entry_uuid");
      }
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.onblocked = () => reject(new Error("the Registrar database is open in another tab; close it and retry"));
  });
}

export class IdbStore {
  constructor(db) {
    this.db = db;
  }
  static async open(name) {
    return new IdbStore(await openDb(name));
  }
  async get(store, key) {
    return req(this.db.transaction(store).objectStore(store).get(key));
  }
  async getAll(store) {
    return req(this.db.transaction(store).objectStore(store).getAll());
  }
  async put(store, value) {
    const v = { ...value };
    const k = keyOf(store);
    if (v[k] == null) delete v[k];
    const tx = this.db.transaction(store, "readwrite");
    const key = await req(tx.objectStore(store).put(v));
    await new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    return key;
  }
  async delete(store, key) {
    const tx = this.db.transaction(store, "readwrite");
    await req(tx.objectStore(store).delete(key));
  }
  async clearAll() {
    const tx = this.db.transaction(STORES, "readwrite");
    for (const s of STORES) tx.objectStore(s).clear();
    await new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  }
}

/** Ask the browser not to evict this origin's data under storage pressure. */
export async function requestPersistence() {
  try {
    if (navigator.storage && navigator.storage.persist) return await navigator.storage.persist();
  } catch {
    /* not supported: fine */
  }
  return false;
}
