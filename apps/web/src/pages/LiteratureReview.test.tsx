import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { LiteratureReviewPage } from "./LiteratureReview";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => vi.restoreAllMocks());
it("records a reasoned decision and displays server PRISMA counts", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("prisma") ? { records_screened: 7, note: "suggestions are not counted" } : { id: "r", title: "Review", criteria: {}, items: [{ candidate: { id: "c", record: { title: "Paper" } }, screening: { title_abstract: { decision: null, suggestion: "include" }, full_text: { decision: null, suggestion: null } } }] });
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "d" }); vi.spyOn(window, "confirm").mockReturnValue(true);
  const p = await renderPage(<LiteratureReviewPage />, "/literature/reviews/:reviewId", "/literature/reviews/r");
  try { expect(p.host.textContent).toContain("7"); await p.fill("reason-c", "Relevant population"); await p.click("Record decision"); expect(post).toHaveBeenCalledWith("/reviews/r/decisions", { candidate_id: "c", stage: "title_abstract", decision: "include", reason: "Relevant population" }, expect.anything()); } finally { await p.dispose(); }
});
it("does not append decisions when confirmation is cancelled", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("prisma") ? {} : { id: "r", title: "Review", criteria: {}, items: [{ candidate: { id: "c", record: {} }, screening: { title_abstract: { decision: null, suggestion: null }, full_text: { decision: null, suggestion: null } } }] });
  const post = vi.spyOn(api, "post").mockResolvedValue({}); vi.spyOn(window, "confirm").mockReturnValue(false);
  const p = await renderPage(<LiteratureReviewPage />, "/literature/reviews/:reviewId", "/literature/reviews/r");
  try { await p.fill("reason-c", "A reason"); await p.click("Record decision"); expect(post).not.toHaveBeenCalled(); } finally { await p.dispose(); }
});
it("shows missing review and PRISMA errors", async () => {
  vi.spyOn(api, "get").mockRejectedValue(new Error("Review not found"));
  const p = await renderPage(<LiteratureReviewPage />, "/literature/reviews/:reviewId", "/literature/reviews/missing");
  try { expect(p.host.textContent).toContain("Review not found"); } finally { await p.dispose(); }
});
