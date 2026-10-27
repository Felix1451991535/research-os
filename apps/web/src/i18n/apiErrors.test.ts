import { afterEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { ApiError, OfflineError, describeError } from "@/api/client";

const err = (code: string, message: string, correlation_id = "") =>
  new ApiError(401, { code, message, retryable: false, correlation_id });

describe("backend error messages in the UI language", () => {
  afterEach(async () => { await i18n.changeLanguage("en"); });

  it("translates a known fixed message by code and keeps the reference id", async () => {
    await i18n.changeLanguage("zh-CN");
    expect(describeError(err("invalid_credentials", "invalid username or password", "c1"))).toBe("用户名或密码错误(参考号 c1)");
    expect(describeError(err("invalid_credentials", "password incorrect"))).toBe("密码错误");
    expect(describeError(new OfflineError())).toContain("无法连接");
  });

  it("falls back to the raw server message for dynamic or unknown messages", async () => {
    await i18n.changeLanguage("zh-CN");
    expect(describeError(err("weak_password", "password must be at least 10 characters"))).toBe("password must be at least 10 characters");
    expect(describeError(err("invalid_credentials", "a changed backend text"))).toBe("a changed backend text");
    expect(describeError(err("brand_new_code", "something new"))).toBe("something new");
  });

  it("English shows the original text", () => {
    expect(describeError(err("version_conflict", "document changed on server; both versions kept, merge required", "x")))
      .toBe("document changed on server; both versions kept, merge required (ref x)");
  });
});
