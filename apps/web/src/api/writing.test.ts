import { describe, expect, it } from "vitest";
import { acceptBlockedReason, listedDocs, type WDoc } from "./writing";

const d = (id: string, kind: string): WDoc => ({ id, project_id: "p", kind, title: id, body: "", version: 1, updated_at: "" });

describe("listedDocs", () => {
  it("filters notes once for the Docs card (review #14)", () => {
    expect(listedDocs([d("a", "note"), d("b", "manuscript"), d("c", "paper_card")]).map((x) => x.id)).toEqual(["b", "c"]);
  });
});

describe("acceptBlockedReason", () => {
  it("blocks Accept while the editor has unsaved edits (review U5)", () => {
    expect(acceptBlockedReason(true)).toMatch(/unsaved/);
    expect(acceptBlockedReason(false)).toBeNull();
  });
});
