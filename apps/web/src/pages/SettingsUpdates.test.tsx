import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/api/client";
import type { UpdateStatus } from "@/api/types";
import { renderPage } from "@/test/pageHarness";
import { UpdatesCard } from "./SettingsUpdates";
afterEach(() => { vi.restoreAllMocks(); });

const base: UpdateStatus = { current_version: "0.1.2", state: "idle", latest: null, progress: null, error: null, mirror_prefix: "",
  feed_url: "https://github.com/o/r/releases/latest/download/latest.json", can_install: true, auto_check: false, installer: null };
const latest = { version: "0.1.3", asset: "ResearchOS-Setup-0.1.3.exe", sha256: "a".repeat(64), url: "https://github.com/x", notes: "Fixes" };

it("shows the version and never checks before the click", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue(base);
  const post = vi.spyOn(api, "post");
  const p = await renderPage(<UpdatesCard />, "/", "/");
  try {
    expect(p.host.textContent).toContain("0.1.2");
    expect(p.host.textContent).toContain("Not checked");
    expect(get).toHaveBeenCalledWith("/update", expect.anything());
    expect(post).not.toHaveBeenCalled();
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
