// Replay of write intents recorded while the host was unreachable (baseline §3). Each replay re-sends the SAME
// idempotency key, so the server returns the original result instead of creating a duplicate; the server
// re-validates everything (approvals, budgets, plan versions). Paid AI requests are never replayed automatically.
import { ApiError, OfflineError, api } from "./client";
import { completeIntent, pendingIntents, type Intent } from "@/offline/intents";

type Sender = (payload: Record<string, unknown>, key: string) => Promise<unknown>;

export const REPLAYABLE: Record<string, Sender> = {
  "project.create": (p, key) => api.post("/projects", p, { idempotencyKey: key }),
  "task.create": (p, key) => api.post("/tasks", { kind: p.kind, payload: p.payload, project_id: p.project_id ?? null },
    { idempotencyKey: key }),
  "note.create": (p, key) => api.post(`/projects/${String(p.project_id)}/notes`, p, { idempotencyKey: key }),
};
export const NOT_AUTO_REPLAYED = new Set(["answer.create"]); // costs money: user must press send again

export interface ReplayReport { sent: string[]; failed: { id: string; code: string }[]; stillOffline: boolean; skipped: string[] }

export async function replayPending(senders: Record<string, Sender> = REPLAYABLE): Promise<ReplayReport> {
  const rep: ReplayReport = { sent: [], failed: [], stillOffline: false, skipped: [] };
  const list = (await pendingIntents()).sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const it of list as Intent[]) {
    const send = senders[it.scope];
    if (!send || NOT_AUTO_REPLAYED.has(it.scope)) { rep.skipped.push(it.id); continue; }
    try {
      await send((it.payload ?? {}) as Record<string, unknown>, it.key);
      await completeIntent(it.id);
      rep.sent.push(it.id);
    } catch (e) {
      if (e instanceof OfflineError) { rep.stillOffline = true; break; }
      if (e instanceof ApiError && !e.body.retryable) await completeIntent(it.id); // definitive: shown to the user
      rep.failed.push({ id: it.id, code: e instanceof ApiError ? e.code : "error" });
    }
  }
  return rep;
}
