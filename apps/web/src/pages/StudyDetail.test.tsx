import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage, study } from "@/test/pageHarness";
import { StudyDetailPage } from "./StudyDetail";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => { vi.restoreAllMocks(); });
const advice = {
  study_id: "s", stage: "search", objective: "score", direction: "max", recommendation: "continue: advance the study to run the proposed batch",
  caveat: "proposals say where to look next; model numbers are estimates, not results",
  sensitivity: { ranking: [{ term: "lr", partial_eta2: 0.81234, p_holm: 0.000123456, reported: true, statement: "lr: effect within this setup" }], basis: "screen ANOVA", notes: [] },
  uncertainty: { seed_sd: 0.0123, config_sd: 0.2, configurations: 10, replicated_configurations: 10, advice: "differences between configurations exceed seed noise: exploring configurations is informative" },
  failure_memory: { rules: [], avoided_regions: ["lr > 0.3 and batch <= 32"], constraints: ["batch <= 64"] },
  proposals: [{ params: { lr: 0.0123456, batch: 32 }, mean: 0.91234, sd: 0.0234, ei: 0.0456 }],
  model: { model: "Matern", max_ei: 0.0456 },
  stop_rules: [{ rule: "budget", met: false, detail: "5 of 10 search configurations used" }, { rule: "patience", met: true, detail: "best value stalled" }],
};
it("preserves effects, NOT ESTIMABLE, unavailable curvature and failure rules", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.endsWith("/advice") ? advice : p === "/tasks" ? [] : study));
  const p = await renderPage(<StudyDetailPage />, "/studies/:studyId", "/studies/s");
  try { expect(p.host.textContent).toContain("effect within this setup"); expect(p.host.textContent).toContain("NOT ESTIMABLE");
    expect(p.host.textContent).toContain("Curvature unavailable"); expect(p.host.textContent).toContain("6 / 6 failed");
    expect(p.host.textContent).not.toContain("no evidence of curvature");
  } finally { await p.dispose(); }
});
it("shows empty analysis and server errors on advance", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.endsWith("/advice") ? advice : p === "/tasks" ? [] : { ...study, data: {} }));
  vi.spyOn(api, "post").mockRejectedValue(new Error("Study cannot advance"));
  const p = await renderPage(<StudyDetailPage />, "/studies/:studyId", "/studies/s");
  try { expect(p.host.textContent).toContain("No ANOVA results yet"); await p.click("Advance study"); expect(p.host.textContent).toContain("Study cannot advance"); } finally { await p.dispose(); }
});
it("requires final confirmation before approving and advancing a study", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.endsWith("/advice") ? advice : p === "/tasks" ? [] : p.includes("subject-hash") ? { hash: "f" } : { ...study, stage: "awaiting_final_approval", data: { frozen: { lr: 0.1 } } }));
  const post = vi.spyOn(api, "post").mockResolvedValue({ stage: "final" });
  const p = await renderPage(<StudyDetailPage />, "/studies/:studyId", "/studies/s");
  try { await p.click("Final evaluation"); expect(post).not.toHaveBeenCalled(); await p.click("Confirm final evaluation");
    expect(post).toHaveBeenCalledWith("/approvals", expect.objectContaining({ subject_type: "final_eval", subject_id: "p@1" }), expect.anything());
    expect(post).toHaveBeenCalledWith("/studies/s/advance", {}, expect.anything());
  } finally { await p.dispose(); }
});

it("shows read-only next-experiment advice with rounded model estimates", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.endsWith("/advice") ? advice : p === "/tasks" ? [] : study));
  const post = vi.spyOn(api, "post");
  const p = await renderPage(<StudyDetailPage />, "/studies/:studyId", "/studies/s");
  try {
    await p.flush();
    const t = p.host.textContent ?? "";
    expect(t).toContain("continue: advance the study"); expect(t).toContain("0.812"); expect(t).toContain("0.000123");
    expect(t).toContain("lr = 0.0123, batch = 32"); expect(t).toContain("0.912 ± 0.0234"); expect(t).toContain("avoid lr > 0.3 and batch <= 32");
    expect(t).toContain("batch <= 64"); expect(t).toContain("met"); expect(t).not.toContain("0.81234");
    expect(post).not.toHaveBeenCalled();
  } finally { await p.dispose(); }
});
it("shows the pre-registered ablation with intervals and skipped factors", async () => {
  const ablation = { data: "validation (confirm seeds); not the test set",
    results: [{ factor: "lr", frozen: 0.00312345, reference: 0.0001, n: 5, mean: 0.1123456, ci95: [0.0812345, 0.1434567], verdict: "contributes (interval above 0)", failed_runs: 0 }],
    plan: { skipped: [{ factor: "dropout", reason: "frozen value already equals the reference" }] } };
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.endsWith("/advice") ? { ...advice, ablation } : p === "/tasks" ? [] : study));
  const p = await renderPage(<StudyDetailPage />, "/studies/:studyId", "/studies/s");
  try { const t = p.host.textContent ?? "";
    expect(t).toContain("0.112 (0.0812 to 0.143)"); expect(t).toContain("contributes (interval above 0)");
    expect(t).toContain("dropout not ablated: frozen value already equals the reference"); expect(t).toContain("not the test set");
  } finally { await p.dispose(); }
});
