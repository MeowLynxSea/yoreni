import type {
  ChatStyle,
  OnboardingStep,
  UserProfile,
} from "@/lib/types";

export const CHAT_STYLE_BASES = [
  "default",
  "professional",
  "friendly",
  "direct",
  "imaginative",
  "pragmatic",
  "custom",
] as const;

export const STYLE_TUNINGS = ["more", "neutral", "less"] as const;

export const STYLE_DIMENSIONS = [
  "affability",
  "enthusiasm",
  "emoji",
  "structure",
] as const;

export const DEFAULT_CHAT_STYLE: ChatStyle = {
  base: "default",
  custom: "",
  affability: "neutral",
  enthusiasm: "neutral",
  emoji: "neutral",
  structure: "neutral",
};

export const DEFAULT_PROFILE: UserProfile = {
  name: "",
  birthday: null,
  gender: null,
  style: DEFAULT_CHAT_STYLE,
  notes: "",
};

export const ONBOARDING_FLOW: OnboardingStep[] = [
  "token",
  "language",
  "intro",
  "name",
  "birthday",
  "gender",
  "style",
  "notes",
  "model",
  "embedding",
];

export function profileFromWire(value: Partial<UserProfile> | null | undefined): UserProfile {
  const style = value?.style ?? DEFAULT_CHAT_STYLE;
  return {
    name: value?.name ?? "",
    birthday: value?.birthday ?? null,
    gender: value?.gender ?? null,
    style: {
      base: style.base ?? "default",
      custom: style.custom ?? "",
      affability: style.affability ?? "neutral",
      enthusiasm: style.enthusiasm ?? "neutral",
      emoji: style.emoji ?? "neutral",
      structure: style.structure ?? "neutral",
    },
    notes: value?.notes ?? "",
  };
}

export function stepIndex(step: OnboardingStep): number {
  const index = ONBOARDING_FLOW.indexOf(step);
  return index < 0 ? 0 : index;
}

export function stepProgress(step: OnboardingStep): number {
  return (stepIndex(step) + 1) / ONBOARDING_FLOW.length;
}

export function nextStep(step: OnboardingStep): OnboardingStep {
  return ONBOARDING_FLOW[Math.min(stepIndex(step) + 1, ONBOARDING_FLOW.length - 1)];
}

export function previousStep(step: OnboardingStep): OnboardingStep {
  return ONBOARDING_FLOW[Math.max(stepIndex(step) - 1, 0)];
}

export function isProfileComplete(profile: UserProfile): boolean {
  return Boolean(profile.name.trim())
    && !(profile.style.base === "custom" && !profile.style.custom.trim());
}
