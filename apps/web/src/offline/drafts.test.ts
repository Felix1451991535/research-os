import "fake-indexeddb/auto";
import { describe, expect, it } from "vitest";
import { allDrafts, dropDraft, loadDraft, saveDraft } from "./drafts";

describe("drafts", () => {
  it("keeps client_operation_id and created_at stable across edits", async () => {
    const a = await saveDraft({ project_id: "p", note_id: "n", base_version: 2, title: "t", body: "one" });
    const b = await saveDraft({ project_id: "p", note_id: "n", base_version: 2, title: "t", body: "two" });
    expect(b.client_operation_id).toBe(a.client_operation_id);
    expect(b.created_at).toBe(a.created_at);
    expect((await loadDraft("p", "n"))?.body).toBe("two");
    await dropDraft("p", "n");
    expect(await allDrafts()).toHaveLength(0);
  });
});
