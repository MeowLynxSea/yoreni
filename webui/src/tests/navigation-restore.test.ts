import { describe, expect, it } from "vitest";

import { restoreLocation } from "@/lib/local-ui-state";

describe("navigation restore", () => {
  it("reopens the remembered page from the bare start route", () => {
    expect(restoreLocation("/", "/projects")).toBe("/projects");
    expect(restoreLocation("/", "/projects/4f1c")).toBe("/projects/4f1c");
    expect(restoreLocation("/", "/settings/memory")).toBe("/settings/memory");
  });

  it("never overrides a deep link", () => {
    expect(restoreLocation("/projects/4f1c", "/projects")).toBeNull();
    expect(restoreLocation("/settings", "/projects")).toBeNull();
  });

  it("ignores a missing or non-app path", () => {
    expect(restoreLocation("/", null)).toBeNull();
    expect(restoreLocation("/", "/")).toBeNull();
    expect(restoreLocation("/", "https://example.com/")).toBeNull();
  });
});
