import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage, revision } from "@/test/pageHarness";
import { ExperimentsPage } from "./Experiments";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: ({ title }: { title: string }) => <h1>{title}</h1> }));
afterEach(() => { vi.restoreAllMocks(); });
it("creates a plan using the reviewed template hash", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/templates" ? [{ id: "smoke", hash: "tpl-hash", status: "reviewed", description: "CPU", params_schema: {} }] : []));
  const post = vi.spyOn(api, "post").mockResolvedValue(revision);
  const p = await renderPage(<ExperimentsPage />, "/experiments", "/experiments");
  try {
    await p.click("Use smoke"); await p.fill("plan-title", "Test plan");
    await p.fill("plan-content", JSON.stringify(revision.content)); await p.click("Create plan");
    expect(post).toHaveBeenCalledWith("/plans", { title: "Test plan", content: revision.content }, expect.objectContaining({ idempotencyKey: expect.any(String) }));
  } finally { await p.dispose(); }
});
it("shows empty templates and plans", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const p = await renderPage(<ExperimentsPage />, "/experiments", "/experiments");
  try { expect(p.host.textContent).toContain("No templates"); expect(p.host.textContent).toContain("No plans"); } finally { await p.dispose(); }
});
it("shows loading and server errors", async () => {
  let reject!: (e: Error) => void;
  vi.spyOn(api, "get").mockImplementation(() => new Promise((_, r) => { reject = r; }));
  const p = await renderPage(<ExperimentsPage />, "/experiments", "/experiments");
  try { expect(p.host.querySelector('[aria-label="Loading"]')).not.toBeNull();
    reject(new Error("Host unavailable")); await p.flush(); expect(p.host.textContent).toContain("Host unavailable");
  } finally { await p.dispose(); }
});
