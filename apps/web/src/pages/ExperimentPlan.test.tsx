import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage, revision } from "@/test/pageHarness";
import { ExperimentPlanPage } from "./ExperimentPlan";
vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
afterEach(() => { vi.restoreAllMocks(); });
it("requires confirmation before approving an immutable revision", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.includes("subject-hash") ? { hash: "hash" } : p === "/tasks" ? [] : revision));
  const post = vi.spyOn(api, "post").mockResolvedValue({});
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try { await p.click("Approve plan"); expect(post).not.toHaveBeenCalled(); await p.click("Confirm approval");
    expect(post).toHaveBeenCalledWith("/approvals", expect.objectContaining({ subject_type: "plan", subject_id: "p@1", expected_hash: "hash" }), expect.anything());
  } finally { await p.dispose(); }
});
it("confirms final evaluation separately and disables duplicate submission", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p.includes("subject-hash") ? { hash: "final-hash" } : p === "/tasks" ? [] : { ...revision, approved_by_revision: 1 }));
  const post = vi.spyOn(api, "post").mockImplementation((p) => p === "/approvals" ? Promise.resolve({}) : new Promise(() => {}));
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try { expect(p.host.textContent).toContain("Sealed test set"); await p.click("Final evaluation"); expect(post).not.toHaveBeenCalled();
    await p.click("Confirm final evaluation"); expect(post).toHaveBeenCalledWith("/approvals", expect.objectContaining({ subject_type: "final_eval" }), expect.anything());
    expect(post).toHaveBeenCalledWith("/plans/p/revisions/1/runs", expect.objectContaining({ stage: "final_eval" }), expect.anything());
    expect([...document.querySelectorAll("button")].find(b => b.textContent === "Confirm final evaluation")?.disabled).toBe(true);
  } finally { await p.dispose(); }
});
it("locks final evaluation when a previous final task succeeded", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/tasks" ? [{ id: "t", kind: "experiment.run", state: "succeeded", payload: { plan_id: "p", stage: "final_eval" } }] : { ...revision, approved_by_revision: 1 }));
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try { expect([...p.host.querySelectorAll("button")].find(b => b.textContent === "Final evaluation")?.disabled).toBe(true); } finally { await p.dispose(); }
});
it("keeps approval errors in the confirmation dialog and allows cancellation", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/tasks" ? [] : revision));
  const post = vi.spyOn(api, "post").mockRejectedValue(new Error("Approval denied"));
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try { await p.click("Approve plan"); await p.click("Confirm approval");
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain("Approval denied");
    await p.click("Cancel"); expect(document.querySelector('[role="alertdialog"]')).toBeNull(); expect(post).toHaveBeenCalledTimes(1);
  } finally { await p.dispose(); }
});
it("recovers remote final approval through reauthentication without automatically launching", async () => {
  const { ApiError } = await import("@/api/client");
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/tasks" ? [] : p.includes("subject-hash") ? { hash: "f" } : { ...revision, approved_by_revision: 1 }));
  const post = vi.spyOn(api, "post").mockImplementation((path) => path === "/approvals" ? Promise.reject(new ApiError(403, { code: "reauth_required", message: "Reauthenticate", retryable: false, correlation_id: "" })) : Promise.resolve({}));
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try { await p.click("Final evaluation"); await p.click("Confirm final evaluation");
    await p.fill("approval-password", "test-only-password"); await p.click("Verify identity");
    expect(post).toHaveBeenCalledWith("/auth/reauth", { password: "test-only-password" }, expect.anything());
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain("Identity confirmed");
    expect(post.mock.calls.some(([path]) => path.endsWith("/runs"))).toBe(false);
  } finally { await p.dispose(); }
});
it("builds a reproducible bundle and lists what was left out", async () => {
  const task = { id: "t1", kind: "experiment.run", state: "failed", payload: { plan_id: "p", stage: "trial", seed: 0 } };
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/tasks" ? [task] : { ...revision, approved_by_revision: 1 }));
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "exp1", status: "ok", runs: 1, failed_runs: 1,
    files: [{ path: "exports/bundle-p-exp1/bundle.zip", mime: "application/zip", sha256: "h" }],
    skipped: [{ file: "runs/r/raw-copy.npz", reason: "possible data, left out" }] });
  const p = await renderPage(<ExperimentPlanPage />, "/experiments/:planId/:revision", "/experiments/p/1");
  try {
    await p.click("Build bundle");
    expect(post).toHaveBeenCalledWith("/plans/p/bundle", {}, expect.objectContaining({ idempotencyKey: expect.any(String) }));
    expect(p.host.textContent).toContain("1 runs, 1 not succeeded");
    expect(document.querySelector('a[href="/api/v1/exports/exp1/files/bundle.zip"]')).not.toBeNull();
    expect(p.host.textContent).toContain("runs/r/raw-copy.npz");
  } finally { await p.dispose(); }
});
