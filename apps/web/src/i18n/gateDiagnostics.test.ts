import { afterEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { diagnosticMessage, severityLabel } from "./gateDiagnostics";

const d = (code: string, message: string, ref: string | null = null) => ({ severity: "error", code, message, ref });

describe("gate diagnostics in the UI language", () => {
  afterEach(async () => { await i18n.changeLanguage("en"); });

  it("shows the server message in English", () => {
    expect(diagnosticMessage(d("free_number", "number '12.5' has no source: ...", "12.5"))).toBe("number '12.5' has no source: ...");
    expect(severityLabel("error")).toBe("error");
  });

  it("translates known codes with their ref and falls back for unknown ones", async () => {
    await i18n.changeLanguage("zh-CN");
    expect(diagnosticMessage(d("unresolved_citation", "citekey 'x' is not in the library", "smith2020"))).toBe("引用键 smith2020 不在文献库中");
    expect(diagnosticMessage(d("slides_overflow", "slide 3 overflows"))).toBe("slide 3 overflows");
    expect(severityLabel("warning")).toBe("警告");
  });
});
