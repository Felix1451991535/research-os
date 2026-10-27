import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, OfflineError, api, describeError } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("refuses writes without an intent key", async () => {
    await expect(api.post("/x", {}, { idempotencyKey: "" })).rejects.toThrow(/intent idempotency key/);
  });
  it("sends CSRF header + idempotency key and maps errors", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ code: "nope", message: "No.", retryable: false, correlation_id: "c1" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    const err = await api.post("/x", { a: 1 }, { idempotencyKey: "k1" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(describeError(err)).toBe("No. (ref c1)");
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>)["idempotency-key"]).toBe("k1");
    expect((init.headers as Record<string, string>)["x-researchos-client"]).toBe("web");
  });
  it("network failure becomes OfflineError with a plain message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.get("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(OfflineError);
    expect(describeError(err)).toMatch(/Can't reach/);
  });
});
