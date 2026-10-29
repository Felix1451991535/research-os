import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/api/client";
import type { UpdateStatus } from "@/api/types";
import { renderPage } from "@/test/pageHarness";
import { WhatsNew } from "@/components/layout/WhatsNew";
import { releaseNotes, UpdatesCard } from "./SettingsUpdates";
afterEach(() => { vi.restoreAllMocks(); });

const base: UpdateStatus = { current_version: "0.1.2", state: "idle", latest: null, progress: null, error: null, mirror_prefix: "",
  feed_url: "https://github.com/o/r/releases/latest/download/latest.json", can_install: true, auto_check: false, installer: null, whats_new: null };
const latest = { version: "0.1.3", asset: "ResearchOS-Setup-0.1.3.exe", sha256: "a".repeat(64), url: "https://github.com/x", notes: "Fixes",
  notes_zh: "" };

it("shows the version and never checks before the click", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue(base);
  const post = vi.spyOn(api, "post");
  const p = await renderPage(<UpdatesCard />, "/", "/");
  try {
    expect(p.host.textContent).toContain("0.1.2");
    expect(p.host.textContent).toContain("Not checked");
    expect(get).toHaveBeenCalledWith("/update", expect.anything());
    expect(post).not.toHaveBeenCalled();
    const link = [...p.host.querySelectorAll("a")].find((a) => a.textContent === "Export diagnostics");
    expect(link?.getAttribute("href")).toBe("/api/v1/diagnostics/bundle");
    expect(link?.hasAttribute("download")).toBe(true);
  } finally { await p.dispose(); }
});

it("checks, then one click downloads and installs once verified", async () => {
  let status: UpdateStatus = base;
  vi.spyOn(api, "get").mockImplementation(async () => status);
  const post = vi.spyOn(api, "post").mockImplementation(async (path: string) => {
    if (path === "/update/check") status = { ...base, state: "available", latest };
    if (path === "/update/download") status = { ...base, state: "ready", latest, progress: { received: 10, total: 10 } };
    if (path === "/update/install") status = { ...base, state: "installing", latest };
    return status;
  });
  const p = await renderPage(<UpdatesCard />, "/", "/");
  try {
    await p.click("Check for updates");
    expect(p.host.textContent).toContain("Update available");
    expect(p.host.textContent).toContain("Fixes");
    await p.click("Update to 0.1.3");
    await p.flush();
    expect(post.mock.calls.map((c) => c[0])).toEqual(["/update/check", "/update/download", "/update/install"]);
    expect(p.host.textContent).toContain("installs the update silently");
  } finally { await p.dispose(); }
});

it("translates update error codes and shows the download path when install is manual", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...base, state: "ready", latest, can_install: false, installer: "C:\\tmp\\ResearchOS-Setup-0.1.3.exe" });
  vi.spyOn(api, "put").mockRejectedValue(new ApiError(400, { code: "update_bad_mirror", message: "raw", retryable: false, correlation_id: "" }));
  const p = await renderPage(<UpdatesCard />, "/", "/");
  try {
    expect(p.host.textContent).toContain("C:\\tmp\\ResearchOS-Setup-0.1.3.exe");
    await p.fill("update-mirror", "http://bad");
    await p.click("Save");
    expect(p.host.textContent).toContain("https:// address ending with /");
  } finally { await p.dispose(); }
});

it("offers the owner a retry without the mirror and never retries by itself", async () => {
  let status: UpdateStatus = { ...base, state: "error", mirror_prefix: "https://ghfast.top/",
    error: { code: "update_mirror_failed", message: "raw" } };
  vi.spyOn(api, "get").mockImplementation(async () => status);
  const post = vi.spyOn(api, "post").mockImplementation(async () => {
    status = { ...base, state: "available", latest };
    return status;
  });
  const p = await renderPage(<UpdatesCard />, "/", "/");
  try {
    expect(p.host.textContent).toContain("Retry without the mirror, or change the mirror.");
    expect(post).not.toHaveBeenCalled();
    await p.click("Retry without the mirror");
    expect(post.mock.calls.map((c) => [c[0], c[1]])).toEqual([["/update/check", { direct: true }]]);
    expect(p.host.textContent).toContain("Update available");
  } finally { await p.dispose(); }
});

it("shows the installed release's notes once and dismisses them", async () => {
  let status: UpdateStatus = { ...base, whats_new: { version: "0.1.2", notes: "New exporter", notes_zh: "新的导出器" } };
  vi.spyOn(api, "get").mockImplementation(async () => status);
  const post = vi.spyOn(api, "post").mockImplementation(async () => { status = { ...base }; return status; });
  const p = await renderPage(<WhatsNew />, "/", "/");
  try {
    expect(document.body.textContent).toContain("What's new in 0.1.2");
    expect(document.body.textContent).toContain("New exporter");
    await p.click("Got it");
    expect(post.mock.calls.map((c) => c[0])).toEqual(["/update/whats-new/dismiss"]);
    expect(document.body.textContent).not.toContain("New exporter");
  } finally { await p.dispose(); }
});

it("picks the Chinese release notes in the zh UI when the feed has them", () => {
  expect(releaseNotes({ notes: "en", notes_zh: "中文" }, "zh-CN")).toBe("中文");
  expect(releaseNotes({ notes: "en", notes_zh: "" }, "zh-CN")).toBe("en");
  expect(releaseNotes({ notes: "en", notes_zh: "中文" }, "en")).toBe("en");
});
