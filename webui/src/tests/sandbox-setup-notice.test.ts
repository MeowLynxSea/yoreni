import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration } from "@/components/onboarding/SandboxSetupNotice";

describe("sandbox setup formatting", () => {
  it("formats byte counts with units", () => {
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1_500_000)).toBe("1.5 MB");
    expect(formatBytes(3_400_000_000)).toBe("3.4 GB");
  });

  it("formats remaining time as minutes and seconds", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(75)).toBe("1:15");
    expect(formatDuration(600)).toBe("10:00");
  });
});
