import { describe, expect, it } from "vitest";

import {
  ONBOARDING_FLOW,
  onboardingFlow,
  previousStep,
  stepIndex,
  stepProgress,
} from "@/lib/onboarding";

describe("onboarding flow", () => {
  it("asks for a password on a host with remote clients", () => {
    expect(onboardingFlow(true)).toBe(ONBOARDING_FLOW);
    expect(previousStep("language", onboardingFlow(true))).toBe("token");
  });

  it("skips the password step on the phone app", () => {
    const flow = onboardingFlow(false);
    expect(flow).not.toContain("token");
    // Going back from the language question lands on the VM step, not a
    // password screen that the app never asked for.
    expect(previousStep("language", flow)).toBe("resources");
    expect(stepIndex("model", flow)).toBe(stepIndex("model") - 1);
    expect(stepProgress("embedding", flow)).toBe(1);
  });
});
