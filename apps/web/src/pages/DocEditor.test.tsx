import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { renderPage } from "@/test/pageHarness";
import { api } from "@/api/client";
import { DocEditorPage } from "./DocEditor";

vi.mock("@/components/layout/AppShell", () => ({ PageHeader: () => null }));
vi.mock("sonner", () => ({ toast: { message: vi.fn(), error: vi.fn(), success: vi.fn() } }));

afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ""; });

it("locks editing throughout delayed acceptance and document refresh", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const doc = { id: "d", project_id: "p", kind: "report", title: "Title", body: "Original", version: 1, updated_at: "" };
  let finishDecision!: (value: { status: string }) => void;
  let finishRefresh!: (value: typeof doc) => void;
  let reads = 0;
  vi.spyOn(api, "get").mockImplementation((path) => {
    if (path === "/docs/d") {
      reads += 1;
      return reads === 1 ? Promise.resolve(doc) : new Promise((resolve) => { finishRefresh = resolve; });
    }
    if (path.includes("suggestions")) return Promise.resolve([{ id: "s", op: "append", text: "Added", author: "a" }]);
    return Promise.resolve({ counts: {}, diagnostics: [], ok: true });
  });
  vi.spyOn(api, "post").mockImplementation(() => new Promise((resolve) => { finishDecision = resolve; }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  try {
    await act(async () => { root.render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/p/d"]}>
      <Routes><Route path="/:id/:docId" element={<DocEditorPage />} /></Routes>
    </MemoryRouter></QueryClientProvider>); });
    for (let i = 0; i < 20 && ![...host.querySelectorAll("button")].some((b) => b.textContent === "Accept"); i++) await flush();
    const accept = [...host.querySelectorAll("button")].find((b) => b.textContent === "Accept");
    if (!accept) throw new Error("Accept button did not render");
    await act(async () => { accept.click(); });
    await flush();
    expect(host.querySelector("textarea")?.disabled).toBe(true);
    expect(host.querySelector("input")?.disabled).toBe(true);
    await act(async () => { finishDecision({ status: "accepted" }); });
    await flush();
    expect(host.querySelector("textarea")?.disabled).toBe(true);
    await act(async () => { finishRefresh({ ...doc, body: "Original\nAdded", version: 2 }); });
    await flush();
    expect(host.querySelector("textarea")?.value).toBe("Original\nAdded");
    expect(host.querySelector("textarea")?.disabled).toBe(false);
  } finally {
    await act(async () => root.unmount()); qc.clear();
  }
});

it.each(["Slides", "Slides draft"])("exports %s and retains overflow diagnostics", async (button) => {
  vi.spyOn(api, "get").mockImplementation(p => Promise.resolve(p === "/docs/d" ? { id: "d", title: "Title", body: "Body", version: 1 } : p.includes("suggestions") ? [] : { counts: {}, diagnostics: [], ok: true }));
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "e", status: "succeeded", files: [{ path: "slides.html" }], diagnostics: [{ severity: "warning", code: "slide_overflow", message: "Content split across slides", ref: null }] });
  const p = await renderPage(<DocEditorPage />, "/:id/:docId", "/p/d");
  try {
    await p.click(button);
    expect(post).toHaveBeenCalledWith("/docs/d/export", { exporter: "report_slides", mode: button === "Slides" ? "final" : "draft", options: { language: "en" } }, expect.objectContaining({ idempotencyKey: expect.any(String) }));
    expect(p.host.textContent).toContain("slide_overflow"); expect(p.host.textContent).toContain("Content split across slides");
    expect(p.host.textContent).toContain("slides.html");
  } finally { await p.dispose(); }
});
