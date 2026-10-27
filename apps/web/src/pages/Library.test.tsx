import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { LibraryPage } from "./Library";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => vi.restoreAllMocks());
it("shows evidence labels for works without PDFs and confirms tier replacement", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => path.startsWith("/library/works") ? [{ id: "w", title: "Metadata paper", weight: ["published", "tier unknown", "retraction status unknown"] }] : []);
  const put = vi.spyOn(api, "put").mockResolvedValue({}); const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const p = await renderPage(<LibraryPage />, "/library", "/library");
  try { expect(p.host.textContent).toContain("Metadata paper"); expect(p.host.textContent).toContain("retraction status unknown");
    await p.fill("tier-source", "JCR"); await p.fill("tier-year", "2025"); await p.fill("tier-entries", '[{"venue":"Journal","tier":"Q1"}]'); await p.click("Replace venue tiers"); expect(put).not.toHaveBeenCalled();
    confirm.mockReturnValue(true); await p.click("Replace venue tiers"); expect(put).toHaveBeenCalledWith("/library/venue-tiers", { source: "JCR", year: 2025, entries: [{ venue: "Journal", tier: "Q1" }] }, expect.anything());
  } finally { await p.dispose(); }
});
it("rejects malformed tiers without writing", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]); const put = vi.spyOn(api, "put");
  const p = await renderPage(<LibraryPage />, "/library", "/library");
  try { expect(p.host.textContent).toContain("No library works yet"); await p.fill("tier-source", "JCR"); await p.fill("tier-year", "2025"); await p.fill("tier-entries", '[{"tier":"Q1"}]'); await p.click("Replace venue tiers"); expect(put).not.toHaveBeenCalled(); expect(p.host.textContent).toContain("tier and venue or ISSN"); } finally { await p.dispose(); }
});
it("checks retraction for exactly one work and surfaces provider results", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => path.startsWith("/library/works") ? [{ id: "w", title: "Paper", weight: ["RETRACTED"] }] : []);
  const post = vi.spyOn(api, "post").mockResolvedValue({ errors: { source: "unavailable" } });
  const p = await renderPage(<LibraryPage />, "/library", "/library");
  try { await p.click("Check retraction: Paper"); expect(post).toHaveBeenCalledWith("/literature/retractions/check", { work_ids: ["w"] }, expect.anything()); expect(p.host.textContent).toContain("unavailable"); } finally { await p.dispose(); }
});
