import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { PairedRuns } from "./PairedRuns";

afterEach(() => { vi.restoreAllMocks(); });

it("compares two runs and shows the interval, McNemar p, power and caveat", async () => {
  const post = vi.spyOn(api, "post").mockResolvedValue({ windows: 45, accuracy_a: 0.5111111, accuracy_b: 0.5777778,
    difference_b_minus_a: 0.0666667, difference_ci95: [-0.0268240, 0.1569300], difference_ci_method: "Newcombe (1998) method 10",
    cohen_h: 0.1339658, minimum_detectable_h_80: 0.5906253, mcnemar: { p_exact: 0.375, p_mid: 0.21875 },
    caveat: "windows from the same subject are not independent", power_note: "two-proportion approximation" });
  const p = await renderPage(<PairedRuns runId="r1" />, "/", "/");
  try {
    await p.fill("paired-other", "r2"); await p.click("Compare");
    expect(post).toHaveBeenCalledWith("/runs/r1/paired", { other_run_id: "r2", split: "search_val" }, expect.anything());
    const t = p.host.textContent ?? "";
    expect(t).toContain("0.06667 (-0.02682 to 0.1569)"); expect(t).toContain("0.375 / 0.2188");
    expect(t).toContain("0.5906"); expect(t).toContain("not independent");
  } finally { await p.dispose(); }
});
