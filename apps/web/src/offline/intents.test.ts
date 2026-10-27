import "fake-indexeddb/auto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, OfflineError } from "@/api/client";
import { pendingIntents, runIntent } from "./intents";

afterEach(() => vi.restoreAllMocks());

describe("runIntent", () => {
  it("reuses the SAME idempotency key when a failed send is retried (no duplicate writes)", async () => {
    const keys: string[] = [];
    await expect(runIntent("x", "op-1", {}, async (k) => { keys.push(k); throw new OfflineError(); })).rejects.toThrow();
    expect(await pendingIntents()).toHaveLength(1); // still pending while offline
    await runIntent("x", "op-1", {}, async (k) => { keys.push(k); return 1; });
    expect(keys[0]).toBe(keys[1]);
    expect(await pendingIntents()).toHaveLength(0);
  });

  it("settles the intent on a definitive 4xx answer but keeps it on 5xx", async () => {
    const body = { code: "bad", message: "bad", retryable: false, correlation_id: "" };
    await expect(runIntent("x", "op-2", {}, async () => { throw new ApiError(422, body); })).rejects.toThrow();
    expect((await pendingIntents()).find((i) => i.id === "op-2")).toBeUndefined();
    await expect(runIntent("x", "op-3", {}, async () => { throw new ApiError(503, { ...body, retryable: true }); })).rejects.toThrow();
    expect((await pendingIntents()).find((i) => i.id === "op-3")).toBeDefined();
  });

  it("different intents get different keys", async () => {
    const keys: string[] = [];
    await runIntent("x", "a", {}, async (k) => keys.push(k));
    await runIntent("x", "b", {}, async (k) => keys.push(k));
    expect(keys[0]).not.toBe(keys[1]);
  });
});
