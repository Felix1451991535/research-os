import "fake-indexeddb/auto";
import { describe, expect, it, vi } from "vitest";
import { OfflineError } from "./client";
import { replayPending } from "./replay";
import { beginIntent, pendingIntents } from "@/offline/intents";

describe("replayPending", () => {
  it("re-sends offline intents with their ORIGINAL idempotency key and clears them", async () => {
    const it1 = await beginIntent("task.create", { kind: "system.sleep", payload: { seconds: 1 } }, "op-a");
    const send = vi.fn(async () => ({}));
    const rep = await replayPending({ "task.create": send });
    expect(send).toHaveBeenCalledWith({ kind: "system.sleep", payload: { seconds: 1 } }, it1.key);
    expect(rep.sent).toEqual(["op-a"]);
    expect(await pendingIntents()).toHaveLength(0);
  });

  it("stops at the first offline failure and keeps the rest", async () => {
    await beginIntent("task.create", {}, "op-b");
    await beginIntent("task.create", {}, "op-c");
    const rep = await replayPending({ "task.create": async () => { throw new OfflineError(); } });
    expect(rep.stillOffline).toBe(true);
    expect((await pendingIntents()).map((i) => i.id).sort()).toEqual(["op-b", "op-c"]);
  });

  it("never auto-replays paid AI requests", async () => {
    await beginIntent("answer.create", { question: "q" }, "op-d");
    const send = vi.fn(async () => ({}));
    const rep = await replayPending({ "answer.create": send, "task.create": async () => ({}) });
    expect(send).not.toHaveBeenCalled();
    expect(rep.skipped).toContain("op-d");
  });
});
