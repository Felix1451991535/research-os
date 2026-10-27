import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { ProjectDecisions } from "./ProjectDecisions";

afterEach(() => { vi.restoreAllMocks(); });

it("shows decisions with support, rejected alternatives and broken links", async () => {
  vi.spyOn(api, "get").mockResolvedValue([
    { id: "d1", kind: "split", question: "How are windows split?", choice: "subject-wise", rationale: "no leakage",
      created_at: new Date().toISOString(), alternatives: [{ option: "window-wise", why_not: "leaks subjects" }],
      support: "owner_judgement", links: [], broken_links: [] },
    { id: "d2", kind: "method", question: "Template?", choice: "CSP", rationale: "reviewed", created_at: new Date().toISOString(),
      alternatives: null, support: "supported", links: [{ ref: "run:r1", live: false }], broken_links: ["run:r1"] }]);
  const p = await renderPage(<ProjectDecisions pid="p1" />, "/", "/");
  try {
    const text = p.host.textContent ?? "";
    expect(text).toContain("Owner judgement"); expect(text).toContain("Supported by evidence");
    expect(text).toContain("Not chosen: window-wise — leaks subjects");
    expect(text).toContain("1 broken link"); expect(text).toContain("Broken: run:r1");
    expect(api.get).toHaveBeenCalledWith("/projects/p1/decisions", expect.anything());
  } finally { await p.dispose(); }
});
