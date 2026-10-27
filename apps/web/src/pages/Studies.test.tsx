import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage, study } from "@/test/pageHarness";
import { StudiesPage } from "./Studies";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => { vi.restoreAllMocks(); });
it("creates a sweep using the selected plan revision and protocol", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const post = vi.spyOn(api, "post").mockResolvedValue(study);
  const p = await renderPage(<StudiesPage />, "/studies", "/studies");
  try { await p.fill("study-plan", "p"); await p.fill("study-protocol", JSON.stringify({ objective: "score", factors: ["lr"] })); await p.click("Start sweep");
    expect(post).toHaveBeenCalledWith("/studies", { plan_id: "p", revision: 1, protocol: { objective: "score", factors: ["lr"] } }, expect.anything());
  } finally { await p.dispose(); }
});
it("rejects malformed protocol without sending a request", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]); const post = vi.spyOn(api, "post");
  const p = await renderPage(<StudiesPage />, "/studies", "/studies");
  try { await p.fill("study-plan", "p"); await p.fill("study-protocol", "[]"); await p.click("Start sweep");
    expect(post).not.toHaveBeenCalled(); expect(p.host.textContent).toContain("JSON object");
  } finally { await p.dispose(); }
});
