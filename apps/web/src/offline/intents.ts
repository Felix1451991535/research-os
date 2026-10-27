// Pending write intents (baseline §3). Each user action gets ONE idempotency key that is persisted until the
// server has acknowledged it, so retries after a network failure or reload can never create duplicates.
import { createStore, del, get, keys, set } from "idb-keyval";
import { ApiError } from "@/api/client";
import { newKey } from "@/lib/utils";

export interface Intent {
  id: string;            // stable client_operation_id of the action
  key: string;           // idempotency key sent to the server (same across retries)
  scope: string;         // e.g. "project.create"
  created_at: string;
  payload?: unknown;     // what the user asked for (shown in "not yet delivered" UI)
}

const store = typeof indexedDB === "undefined" ? undefined : createStore("researchos", "intents");

export async function beginIntent(scope: string, payload?: unknown, id: string = newKey()): Promise<Intent> {
  const existing = store ? await get<Intent>(id, store) : undefined;
  if (existing) return existing;
  const intent: Intent = { id, key: newKey(), scope, created_at: new Date().toISOString(), payload };
  if (store) await set(id, intent, store);
  return intent;
}

export async function completeIntent(id: string): Promise<void> {
  if (store) await del(id, store);
}

export async function pendingIntents(): Promise<Intent[]> {
  if (!store) return [];
  const ids = await keys<string>(store);
  const all = await Promise.all(ids.map((k) => get<Intent>(k, store)));
  return all.filter((x): x is Intent => Boolean(x));
}

/**
 * Run a write under a persisted intent: the same intent id always sends the same idempotency key.
 * The intent is cleared on success or on a definitive (non-retryable) server answer; kept when offline.
 */
export async function runIntent<T>(scope: string, intentId: string, payload: unknown, send: (key: string) => Promise<T>): Promise<T> {
  const intent = await beginIntent(scope, payload, intentId);
  try {
    const out = await send(intent.key);
    await completeIntent(intent.id);
    return out;
  } catch (e) {
    // Definitive server answer (4xx not retryable): the intent is settled. Offline / 5xx: keep the key for retry.
    if (e instanceof ApiError && !e.body.retryable && e.status < 500) await completeIntent(intent.id);
    throw e;
  }
}
