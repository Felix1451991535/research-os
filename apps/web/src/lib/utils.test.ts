import { describe, expect, it } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("keeps a type-scale size together with a semantic color", () => {
    expect(cn("text-caption text-info")).toBe("text-caption text-info");
    expect(cn("text-body text-caption")).toBe("text-caption");
  });
});
