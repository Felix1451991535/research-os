import { act } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { LibraryEvidence } from "./LibraryEvidence";

afterEach(() => { vi.restoreAllMocks(); });

it("creates a methods checklist for a work in the chosen project after confirmation", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.startsWith("/library/works")
    ? [{ id: "w1", title: "Paper", weight: ["published"] }] : p === "/projects" ? [{ id: "p1", title: "V1" }] : []));
  const post = vi.spyOn(api, "post").mockResolvedValue({ doc: { id: "d9" }, fields: {} });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const p = await renderPage(<LibraryEvidence />, "/library", "/library");
  try {
    const select = document.getElementById("card-project") as HTMLSelectElement;
    await act(async () => { select.value = "p1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await p.click("Methods checklist");
    expect(confirm.mock.calls[0]?.[0]).toContain("paid");
    expect(post).toHaveBeenCalledWith("/works/w1/methods-checklist", { project_id: "p1" },
      expect.objectContaining({ idempotencyKey: expect.any(String) }));
    expect(document.body.textContent).toContain("Destination");  // navigated to the new doc
  } finally { await p.dispose(); }
});
