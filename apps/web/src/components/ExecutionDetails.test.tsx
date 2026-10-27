import { expect, it } from "vitest";
import { renderPage } from "@/test/pageHarness";
import { formatNumber, humanize } from "@/lib/utils";
import { JsonDetails } from "./ExecutionDetails";

it("formats numbers for reading and humanizes keys", () => {
  expect(formatNumber(0.3382352941176471)).toBe("0.3382");
  expect(formatNumber(12000)).toBe((12000).toLocaleString());
  expect(formatNumber(0.00001234)).toBe("1.234e-5");
  expect(humanize("split_hash")).toBe("Split hash");
});

it("shows structured data with labels, folded long lists and raw JSON on demand", async () => {
  const value = { split_hash: "abc", macro_f1: 0.3382352941176471, seeds: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], ok: true, note: null };
  const p = await renderPage(<JsonDetails value={value} />, "/", "/");
  try {
    const text = document.body.textContent ?? "";
    expect(text).toContain("Split hash");
    expect(text).toContain("0.3382");
    expect(text).toContain("yes");
    expect(text).toContain("Show all 10");
    expect(text).not.toContain("9, 10");  // folded to the first 8
    await p.click("Show all 10");
    expect(document.body.textContent).toContain("9, 10");
    expect(document.querySelector("details pre")?.textContent).toContain('"split_hash": "abc"');
  } finally { await p.dispose(); }
});
