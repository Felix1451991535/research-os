import { act, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { parseAxes, ResearchPlanning } from "./ResearchPlanning";

afterEach(() => { vi.restoreAllMocks(); });

it("parses gap-map axes with alternatives and rejects malformed lines", () => {
  expect(parseAxes("paradigm: motor imagery, sleep staging | sleep scoring\nmethod: CSP")).toEqual(
    { paradigm: ["motor imagery", ["sleep staging", "sleep scoring"]], method: ["CSP"] });
  expect(() => parseAxes("no colon here")).toThrow(/name: term/);
});

it("checks feasibility after confirmation and maps gaps without a model", async () => {
  vi.spyOn(api, "get").mockImplementation((p) => Promise.resolve(p === "/datasets" ? [{ id: "ds1", name: "eegmmi", version: "1" }] : []));
  const post = vi.spyOn(api, "post").mockImplementation((p) => Promise.resolve(p.endsWith("/feasibility")
    ? { doc: { id: "d1" }, status: { data: { status: "ok", reason: "licence recorded" }, time: { status: "unknown", reason: "no comparable run" } } }
    : { claims_used: 3, counts: {}, cells: [{ terms: { paradigm: "motor imagery" }, status: "thin", works: ["w1"] }] }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const p = await renderPage(<ResearchPlanning pid="p1" />, "/", "/");
  try {
    await p.fill("feas-topic", "motor imagery decoding");
    await p.click("Check feasibility");
    expect(post).toHaveBeenCalledWith("/projects/p1/feasibility", expect.objectContaining({ topic: "motor imagery decoding" }), expect.anything());
    expect(p.host.textContent).toContain("data: ok"); expect(p.host.textContent).toContain("no comparable run");
    await p.fill("gap-axes", "paradigm: motor imagery");
    await p.click("Map evidence gaps");
    expect(post).toHaveBeenCalledWith("/claims/gap-map", { project_id: "p1", axes: { paradigm: ["motor imagery"] } }, expect.anything());
    expect(p.host.textContent).toContain("thin"); expect(p.host.textContent).toContain("not absence from the literature");
  } finally { await p.dispose(); }
});

function Switcher() {
  const [pid, setPid] = useState("A");
  return <><button onClick={() => setPid("B")}>Go to B</button><ResearchPlanning pid={pid} /></>;
}

it("never shows project A's feasibility under project B (review #78), finished or late", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let resolveLate: (v: unknown) => void = () => {};
  const post = vi.spyOn(api, "post")
    .mockResolvedValueOnce({ doc: { id: "docA" }, status: { data: { status: "ok", reason: "A facts" } } })
    .mockImplementationOnce(() => new Promise(r => { resolveLate = r; }));
  // finished result, then switch
  let p = await renderPage(<Switcher />, "/", "/");
  try {
    await p.fill("feas-topic", "topic A"); await p.click("Check feasibility");
    expect(p.host.textContent).toContain("A facts");
    await p.click("Go to B");
    expect(p.host.textContent).not.toContain("A facts");
    expect(document.querySelector('a[href*="docA"]')).toBeNull();
  } finally { await p.dispose(); }
  // late response: request for A still pending when the user switches to B
  p = await renderPage(<Switcher />, "/", "/");
  try {
    await p.fill("feas-topic", "topic A");
    const btn = [...document.querySelectorAll("button")].find(b => b.textContent === "Check feasibility");
    await act(async () => { btn?.click(); });
    await p.click("Go to B");
    await act(async () => { resolveLate({ doc: { id: "docLate" }, status: { data: { status: "ok", reason: "late A" } } }); });
    await p.flush();
    expect(p.host.textContent).not.toContain("late A");
    expect(document.querySelector('a[href*="docLate"]')).toBeNull();
    expect(post).toHaveBeenCalledTimes(2);
  } finally { await p.dispose(); }
});
