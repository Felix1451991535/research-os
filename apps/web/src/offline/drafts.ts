// Offline note drafts (baseline §3): client_operation_id + base_version + timestamps. Server stays authoritative;
// a version conflict keeps both texts and asks the user to merge.
import { createStore, del, entries, get, set } from "idb-keyval";
import { newKey } from "@/lib/utils";

export interface Draft {
  key: string;                 // `${project_id}:${note_id ?? "new"}`
  project_id: string;
  note_id: string | null;
  base_version: number | null;
  title: string;
  body: string;
  client_operation_id: string;
  created_at: string;
  updated_at: string;
}

const store = typeof indexedDB === "undefined" ? undefined : createStore("researchos-drafts", "drafts");

export function draftKey(projectId: string, noteId: string | null): string {
  return `${projectId}:${noteId ?? "new"}`;
}

export async function saveDraft(d: Omit<Draft, "key" | "client_operation_id" | "created_at" | "updated_at"> &
  Partial<Pick<Draft, "client_operation_id" | "created_at">>): Promise<Draft> {
  const key = draftKey(d.project_id, d.note_id);
  const prev = store ? await get<Draft>(key, store) : undefined;
  const now = new Date().toISOString();
  const out: Draft = {
    ...d, key,
    client_operation_id: prev?.client_operation_id ?? d.client_operation_id ?? newKey(),
    created_at: prev?.created_at ?? now,
    updated_at: now,
  };
  if (store) await set(key, out, store);
  return out;
}

export async function loadDraft(projectId: string, noteId: string | null): Promise<Draft | undefined> {
  return store ? get<Draft>(draftKey(projectId, noteId), store) : undefined;
}

export async function dropDraft(projectId: string, noteId: string | null): Promise<void> {
  if (store) await del(draftKey(projectId, noteId), store);
}

export async function allDrafts(): Promise<Draft[]> {
  if (!store) return [];
  return (await entries<string, Draft>(store)).map(([, v]) => v);
}
