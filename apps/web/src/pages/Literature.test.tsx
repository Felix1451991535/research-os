import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { LiteraturePage } from "./Literature";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => vi.restoreAllMocks());
it("searches all sources and chases references", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "run", candidates: [{ id: "c", record: { title: "Paper" } }], per_source: { crossref: { error: "rate limited" } } });
  const p = await renderPage(<LiteraturePage />, "/literature", "/literature");
  try { await p.fill("lit-query", "science"); await p.click("Search");
    expect(post).toHaveBeenCalledWith("/literature/search", expect.objectContaining({ text: "science", sources: ["lit_crossref", "lit_openalex", "lit_pubmed", "lit_arxiv", "lit_semantic"] }), expect.anything());
    expect(p.host.textContent).toContain("rate limited"); await p.click("References (backward)");
    expect(post).toHaveBeenLastCalledWith("/literature/chain", expect.objectContaining({ candidate_id: "c", direction: "references" }), expect.anything());
  } finally { await p.dispose(); }
});
it("shows search failures and saved-search empty state", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]); vi.spyOn(api, "post").mockRejectedValue(new Error("Search failed"));
  const p = await renderPage(<LiteraturePage />, "/literature", "/literature");
  try { expect(p.host.textContent).toContain("No saved searches"); await p.fill("lit-query", "science"); await p.click("Search"); expect(p.host.textContent).toContain("Search failed"); } finally { await p.dispose(); }
});
it("saves the query, toggles scheduling, and queues a run", async () => {
  vi.spyOn(api, "get").mockResolvedValue([{ id: "ss", name: "Watch", query: { text: "science" }, sources: ["lit_crossref"], cadence: "manual", enabled: true, last_run_at: null }]);
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "task" }); const put = vi.spyOn(api, "put").mockResolvedValue({});
  const p = await renderPage(<LiteraturePage />, "/literature", "/literature");
  try { await p.fill("lit-query", "science"); await p.fill("saved-name", "Watch"); await p.click("Save search");
    expect(post).toHaveBeenCalledWith("/saved-searches", expect.objectContaining({ name: "Watch", query: { text: "science" }, kind: "query", cadence: "manual" }), expect.anything());
    await p.click("Disable"); expect(put).toHaveBeenCalledWith("/saved-searches/ss/enabled", { enabled: false }, expect.anything());
    await p.click("Run now"); expect(post).toHaveBeenCalledWith("/saved-searches/ss/run", {}, expect.anything()); expect(p.host.textContent).toContain("Queued task task");
  } finally { await p.dispose(); }
});
it("shows pending, empty, and failed saved-search reads", async () => {
  vi.spyOn(api, "get").mockRejectedValue(new Error("Saved searches unavailable"));
  vi.spyOn(api, "post").mockResolvedValue({ id: "r", candidates: [], per_source: {} });
  const p = await renderPage(<LiteraturePage />, "/literature", "/literature");
  try { expect(p.host.textContent).toContain("Saved searches unavailable"); await p.fill("lit-query", "none"); await p.click("Search"); expect(p.host.textContent).toContain("No candidates found"); } finally { await p.dispose(); }
});
