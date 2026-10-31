import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, Loader2, Sparkles } from "lucide-react";

import { AmbientBackground } from "@/components/AmbientBackground";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { BirthdayField } from "@/components/profile/BirthdayField";
import { ChatStyleEditor } from "@/components/profile/ChatStyleEditor";
import { OnboardingEmbeddingStep } from "@/components/onboarding/OnboardingEmbeddingStep";
import { OnboardingModelStep } from "@/components/onboarding/OnboardingModelStep";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  completeOnboarding,
  saveOnboardingProfile,
  saveOnboardingStep,
  saveOnboardingToken,
} from "@/lib/api";
import {
  isProfileComplete,
  previousStep,
  profileFromWire,
  stepProgress,
} from "@/lib/onboarding";
import type { YoreniClient } from "@/lib/yoreni-client";
import type { OnboardingSnapshot, OnboardingStep, UserProfile } from "@/lib/types";
import { cn } from "@/lib/utils";

import "./onboarding.css";

type Busy = "idle" | "saving" | "finishing";

export function OnboardingFlow({
  client,
  apiToken,
  initial,
  onTokenSet,
  onComplete,
}: {
  client: YoreniClient;
  apiToken: string;
  initial: OnboardingSnapshot;
  onTokenSet: (token: string) => void;
  onComplete: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [step, setStep] = useState<OnboardingStep>(initial.step === "done" ? "embedding" : initial.step);
  const [profile, setProfile] = useState<UserProfile>(() => profileFromWire(initial.profile));
  const [tokenSet, setTokenSet] = useState(initial.token_set);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [busy, setBusy] = useState<Busy>("idle");
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState(client.status === "open");
  const completeTimer = useRef<number | null>(null);
  const advanceTimer = useRef<number | null>(null);
  const firstDraft = useRef(true);

  useEffect(() => client.onStatus((status) => setOnline(status === "open")), [client]);
  useEffect(() => () => {
    if (completeTimer.current !== null) window.clearTimeout(completeTimer.current);
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
  }, []);
  // Keep the current answer durable even before the user presses Continue.
  useEffect(() => {
    if (firstDraft.current) {
      firstDraft.current = false;
      return;
    }
    if (!online || step === "token" || step === "done") return;
    const timer = window.setTimeout(() => {
      void saveOnboardingProfile(client, profile).catch(() => {});
    }, 500);
    return () => window.clearTimeout(timer);
  }, [client, online, profile, step]);

  const update = (patch: Partial<UserProfile>) => setProfile((current) => ({ ...current, ...patch }));
  const failure = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));

  const persist = async (next: OnboardingStep, value: UserProfile = profile) => {
    await saveOnboardingProfile(client, value, next);
    setStep(next);
  };

  const submit = async (next: OnboardingStep, value?: Partial<UserProfile>) => {
    if (busy !== "idle" || !online) return;
    setError(null);
    setBusy("saving");
    const merged = { ...profile, ...value };
    try {
      await persist(next, merged);
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy("idle");
    }
  };

  const back = async () => {
    if (busy !== "idle") return;
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
    const target = previousStep(step);
    setError(null);
    setStep(target);
    try {
      await saveOnboardingStep(client, target);
    } catch {
      // Navigation is local; the stored step catches up on the next save.
    }
  };

  const savePassword = async () => {
    if (busy !== "idle" || !online) return;
    const value = password.trim();
    if (!value && tokenSet) {
      setBusy("saving");
      try {
        await saveOnboardingStep(client, "intro");
        setStep("intro");
      } catch (reason) {
        failure(reason);
      } finally {
        setBusy("idle");
      }
      return;
    }
    if (value.length < 8) {
      setError(t("onboarding.token.tooShort"));
      return;
    }
    if (value !== confirmation.trim()) {
      setError(t("onboarding.token.mismatch"));
      return;
    }
    setError(null);
    setBusy("saving");
    try {
      await saveOnboardingToken(client, value);
      onTokenSet(value);
      setTokenSet(true);
      setPassword("");
      setConfirmation("");
      setStep("intro");
    } catch (reason) {
      failure(reason);
    } finally {
      setBusy("idle");
    }
  };

  const chooseGender = (gender: UserProfile["gender"]) => {
    if (busy !== "idle" || !online) return;
    update({ gender });
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
    advanceTimer.current = window.setTimeout(() => {
      void submit("style", { gender });
    }, 380);
  };

  const finish = async () => {
    if (busy !== "idle" || !online) return;
    if (!isProfileComplete(profile)) {
      setError(t("onboarding.errors.nameRequired"));
      setStep("name");
      return;
    }
    setError(null);
    setBusy("finishing");
    try {
      await completeOnboarding(client, i18n.resolvedLanguage ?? "en");
      setStep("done");
      completeTimer.current = window.setTimeout(onComplete, 1800);
    } catch (reason) {
      failure(reason);
      setStep("model");
      void saveOnboardingStep(client, "model").catch(() => {});
      setBusy("idle");
    }
  };

  const progress = useMemo(() => stepProgress(step), [step]);
  const stepNumber = Math.round(progress * 9);

  if (step === "done") {
    return (
      <main className="onboarding-shell flex h-full flex-col overflow-y-auto overscroll-contain px-6 pb-[max(2rem,env(safe-area-inset-bottom))]">
        <AmbientBackground />
        <div className="relative z-10 my-auto flex flex-col items-center text-center">
          <div className="onboarding-complete-mark relative grid h-24 w-24 place-items-center">
            <span className="onboarding-complete-ring absolute inset-0 rounded-full border border-foreground/15" />
            <span className="grid h-20 w-20 place-items-center rounded-full bg-foreground text-background shadow-[0_24px_60px_-24px_rgba(20,32,48,0.65)]">
              <Check className="h-8 w-8" strokeWidth={2.2} aria-hidden />
            </span>
          </div>
          <h1 className="display-serif mt-8 text-[clamp(2rem,6vw,2.75rem)] leading-tight text-foreground/95">
            {t("onboarding.done.title")}
          </h1>
          <p className="mt-3 max-w-sm text-[14px] leading-6 text-muted-foreground">
            {t("onboarding.done.body", { name: profile.name })}
          </p>
        </div>
      </main>
    );
  }

  const isQuestion = ["name", "birthday", "gender", "notes"].includes(step);
  const canContinue = online && busy === "idle";

  return (
    <main className="onboarding-shell flex h-full flex-col overflow-y-auto overscroll-contain px-5 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-[max(1.25rem,env(safe-area-inset-top))] sm:px-8">
      <AmbientBackground />
      <div className="onboarding-orb onboarding-orb--one" aria-hidden />
      <div className="onboarding-orb onboarding-orb--two" aria-hidden />

      <header className="relative z-10 mx-auto flex w-full max-w-3xl items-center gap-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <img
            src="/brand/yoreni_mark.svg"
            alt=""
            draggable={false}
            className="h-8 w-8 select-none rounded-[0.6rem]"
          />
          <span className="display-serif text-[1.35rem] leading-none text-foreground/90">
            Yoreni
          </span>
        </div>
        <div className="flex flex-1 items-center gap-3">
          <div className="onboarding-progress flex-1">
            <span style={{ width: `${Math.max(progress * 100, 4)}%` }} />
          </div>
          <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground/70">
            {stepNumber}/9
          </span>
        </div>
        <LanguageSwitcher collapsed className="shrink-0 text-muted-foreground" />
      </header>

      <div className="relative z-10 mx-auto flex w-full max-w-3xl flex-1 flex-col py-8 sm:py-12">
        <div className="my-auto w-full">
        {!online ? (
          <p className="mx-auto mb-5 flex w-fit items-center gap-2 rounded-full border border-foreground/10 bg-white/45 px-3.5 py-1.5 text-[12px] text-muted-foreground backdrop-blur-md dark:border-white/10 dark:bg-white/[0.05]">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("app.loading.connecting")}
          </p>
        ) : null}
        {error ? (
          <div
            role="alert"
            className="onboarding-field-enter mx-auto mb-5 w-full max-w-xl rounded-2xl border border-destructive/20 bg-destructive/8 px-4 py-3 text-[13px] leading-5 text-destructive"
          >
            {error}
          </div>
        ) : null}

        {step === "token" ? (
          <section key="token" className="onboarding-stage mx-auto w-full max-w-xl">
            <div className="onboarding-stage-stack">
              <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70" style={{ "--onboarding-index": 0 } as CSSProperties}>
                {t("onboarding.token.eyebrow")}
              </p>
              <h1 className="display-serif mt-4 text-[clamp(2.2rem,6vw,3.2rem)] leading-[1.05] text-foreground/95" style={{ "--onboarding-index": 1 } as CSSProperties}>
                {t("onboarding.token.title")}
              </h1>
              <p className="mt-4 max-w-md text-[14.5px] leading-7 text-muted-foreground" style={{ "--onboarding-index": 2 } as CSSProperties}>
                {t("onboarding.token.body")}
              </p>
            </div>

            <form
              className="onboarding-stage mt-9 space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                void savePassword();
              }}
            >
              <div className="relative">
                <Input
                  type={passwordVisible ? "text" : "password"}
                  value={password}
                  onChange={(event) => { setPassword(event.target.value); setError(null); }}
                  placeholder={tokenSet ? t("onboarding.token.keepPlaceholder") : t("onboarding.token.placeholder")}
                  autoComplete="new-password"
                  aria-label={t("onboarding.token.label")}
                  disabled={busy !== "idle"}
                  autoFocus
                  className="h-14 rounded-full border-foreground/10 bg-white/55 pl-5 pr-14 text-[15px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
                />
                <button
                  type="button"
                  onClick={() => setPasswordVisible((visible) => !visible)}
                  aria-label={t(passwordVisible ? "onboarding.token.hide" : "onboarding.token.show")}
                  className="absolute right-2 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  {passwordVisible
                    ? <EyeOff className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                    : <Eye className="h-4 w-4" strokeWidth={1.75} aria-hidden />}
                </button>
              </div>
              <Input
                type={passwordVisible ? "text" : "password"}
                value={confirmation}
                onChange={(event) => { setConfirmation(event.target.value); setError(null); }}
                placeholder={t("onboarding.token.confirmPlaceholder")}
                autoComplete="new-password"
                aria-label={t("onboarding.token.confirmLabel")}
                disabled={busy !== "idle"}
                className="h-14 rounded-full border-foreground/10 bg-white/55 px-5 text-[15px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
              />
              <div className="flex items-center justify-between gap-4 pt-2">
                <p className="max-w-[16rem] text-[12px] leading-5 text-muted-foreground/80">
                  {tokenSet ? t("onboarding.token.alreadySet") : t("onboarding.token.hint")}
                </p>
                <Button
                  type="submit"
                  disabled={!canContinue || (!password.trim() && !tokenSet)}
                  className="h-11 shrink-0 rounded-full px-6"
                >
                  {busy === "saving"
                    ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    : <ArrowRight className="h-4 w-4" aria-hidden />}
                  <span>{t("onboarding.continue")}</span>
                </Button>
              </div>
            </form>
            <p className="mt-6 text-center text-[11.5px] text-muted-foreground/60">
              {t("onboarding.token.footnote")}
            </p>
          </section>
        ) : null}

        {step === "intro" ? (
          <section key="intro" className="onboarding-stage mx-auto w-full max-w-2xl">
            <div className="space-y-6 sm:space-y-7">
              {[0, 1, 2].map((index) => (
                <p
                  key={index}
                  className={cn(
                    "onboarding-intro-line display-serif text-balance",
                    index === 0
                      ? "text-[clamp(2rem,5.4vw,3.1rem)] leading-[1.15] text-foreground/95"
                      : "text-[clamp(1.35rem,3.4vw,1.9rem)] leading-[1.45] text-foreground/70",
                  )}
                  style={{ "--onboarding-index": index } as CSSProperties}
                >
                  {t(`onboarding.intro.line${index + 1}`)}
                </p>
              ))}
            </div>
            <div className="mt-10 flex items-center gap-4 sm:mt-14">
              <Button
                onClick={() => void submit("name")}
                disabled={!canContinue}
                className="h-12 rounded-full px-7 text-[14px]"
              >
                <Sparkles className="h-4 w-4" aria-hidden />
                <span>{t("onboarding.intro.action")}</span>
              </Button>
              <button
                type="button"
                onClick={() => void back()}
                className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
              >
                {t("onboarding.back")}
              </button>
            </div>
          </section>
        ) : null}

        {isQuestion && step === "name" ? (
          <QuestionStage
            key="name"
            eyebrow={t("onboarding.name.eyebrow")}
            question={t("onboarding.name.question")}
            hint={t("onboarding.name.hint")}
            canContinue={Boolean(profile.name.trim()) && canContinue}
            busy={busy === "saving"}
            onBack={() => void back()}
            onSubmit={() => void submit("birthday", { name: profile.name.trim() })}
          >
            <Input
              value={profile.name}
              onChange={(event) => update({ name: event.target.value })}
              placeholder={t("onboarding.name.placeholder")}
              maxLength={80}
              autoFocus
              autoComplete="given-name"
              aria-label={t("onboarding.name.question")}
              className="h-14 rounded-full border-foreground/10 bg-white/55 px-6 text-[16px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
            />
          </QuestionStage>
        ) : null}

        {isQuestion && step === "birthday" ? (
          <QuestionStage
            key="birthday"
            eyebrow={t("onboarding.birthday.eyebrow")}
            question={t("onboarding.birthday.question")}
            hint={t("onboarding.birthday.hint")}
            canContinue={canContinue}
            busy={busy === "saving"}
            onBack={() => void back()}
            onSubmit={() => void submit("gender")}
          >
            <BirthdayField value={profile.birthday} onChange={(birthday) => update({ birthday })} />
            <div className="mt-4 flex items-center gap-4">
              <button
                type="button"
                onClick={() => {
                  update({ birthday: null });
                  void submit("gender", { birthday: null });
                }}
                className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
              >
                {t("onboarding.skip")}
              </button>
            </div>
          </QuestionStage>
        ) : null}

        {isQuestion && step === "gender" ? (
          <QuestionStage
            key="gender"
            eyebrow={t("onboarding.gender.eyebrow")}
            question={t("onboarding.gender.question")}
            hint={t("onboarding.gender.hint")}
            canContinue={canContinue}
            busy={busy === "saving"}
            onBack={() => void back()}
            onSubmit={() => void submit("style")}
          >
            <div className="flex flex-wrap gap-2.5">
              {(["male", "female", "undisclosed"] as const).map((gender) => (
                <button
                  key={gender}
                  type="button"
                  data-selected={profile.gender === gender}
                  onClick={() => chooseGender(gender)}
                  className={cn(
                    "onboarding-choice min-w-[7.5rem] rounded-2xl border px-5 py-3.5 text-[14px] transition-colors",
                    profile.gender === gender
                      ? "border-foreground/30 bg-foreground text-background"
                      : "border-foreground/10 bg-white/45 text-foreground/85 hover:border-foreground/25 dark:border-white/10 dark:bg-white/[0.05]",
                  )}
                >
                  {t(`onboarding.gender.${gender}`)}
                </button>
              ))}
            </div>
          </QuestionStage>
        ) : null}

        {step === "style" ? (
          <section key="style" className="onboarding-stage mx-auto w-full max-w-2xl">
            <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70">
              {t("onboarding.style.eyebrow")}
            </p>
            <h1 className="display-serif mt-3 text-[clamp(1.9rem,4.6vw,2.6rem)] leading-[1.15] text-foreground/95">
              {t("onboarding.style.question")}
            </h1>
            <p className="mt-2 text-[13.5px] leading-6 text-muted-foreground">
              {t("onboarding.style.hint")}
            </p>
            <div className="mt-7">
              <ChatStyleEditor
                value={profile.style}
                onChange={(style) => update({ style })}
                idPrefix="onboarding-style"
              />
            </div>
            <StageActions
              canContinue={canContinue && !(profile.style.base === "custom" && !profile.style.custom.trim())}
              busy={busy === "saving"}
              onBack={() => void back()}
              onSubmit={() => void submit("notes")}
            />
          </section>
        ) : null}

        {step === "notes" ? (
          <QuestionStage
            key="notes"
            eyebrow={t("onboarding.notes.eyebrow")}
            question={t("onboarding.notes.question")}
            hint={t("onboarding.notes.hint")}
            canContinue={canContinue}
            busy={busy === "saving"}
            onBack={() => void back()}
            onSubmit={() => void submit("model")}
          >
            <Textarea
              value={profile.notes}
              onChange={(event) => update({ notes: event.target.value })}
              placeholder={t("onboarding.notes.placeholder")}
              rows={5}
              maxLength={2000}
              autoFocus
              className="rounded-3xl border-foreground/10 bg-white/55 px-5 py-4 text-[15px] leading-7 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
            />
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                onClick={() => void submit("model", { notes: "" })}
                className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
              >
                {t("onboarding.skip")}
              </button>
            </div>
          </QuestionStage>
        ) : null}

        {step === "model" ? (
          <section key="model" className="onboarding-stage mx-auto w-full max-w-2xl">
            <OnboardingModelStep
              client={client}
              apiToken={apiToken}
              online={online}
              onBack={() => void back()}
              onDone={() => void submit("embedding")}
            />
          </section>
        ) : null}

        {step === "embedding" ? (
          <section key="embedding" className="onboarding-stage mx-auto w-full max-w-2xl">
            <OnboardingEmbeddingStep
              client={client}
              apiToken={apiToken}
              online={online}
              finishLabel={t("onboarding.embedding.finish")}
              busy={busy === "finishing"}
              onBack={() => void back()}
              onDone={() => void finish()}
            />
          </section>
        ) : null}
        </div>
      </div>
    </main>
  );
}

function QuestionStage({
  eyebrow,
  question,
  hint,
  children,
  canContinue,
  busy,
  onBack,
  onSubmit,
}: {
  eyebrow: string;
  question: string;
  hint: string;
  children: ReactNode;
  canContinue: boolean;
  busy: boolean;
  onBack: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="onboarding-stage mx-auto w-full max-w-xl">
      <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70">{eyebrow}</p>
      <h1 className="display-serif mt-4 text-[clamp(2rem,5.6vw,3rem)] leading-[1.1] text-foreground/95">
        {question}
      </h1>
      <p className="mt-3 text-[13.5px] leading-6 text-muted-foreground">{hint}</p>
      <form
        className="mt-8"
        onSubmit={(event) => {
          event.preventDefault();
          if (canContinue) onSubmit();
        }}
      >
        {children}
        <div className="mt-8 flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            {t("onboarding.back")}
          </button>
          <Button type="submit" disabled={!canContinue} className="h-11 rounded-full px-6">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ArrowRight className="h-4 w-4" aria-hidden />}
            <span>{t("onboarding.continue")}</span>
          </Button>
        </div>
      </form>
    </section>
  );
}

function StageActions({
  canContinue,
  busy,
  onBack,
  onSubmit,
}: {
  canContinue: boolean;
  busy: boolean;
  onBack: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-8 flex items-center justify-between gap-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        {t("onboarding.back")}
      </button>
      <Button onClick={onSubmit} disabled={!canContinue} className="h-11 rounded-full px-6">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ArrowRight className="h-4 w-4" aria-hidden />}
        <span>{t("onboarding.continue")}</span>
      </Button>
    </div>
  );
}
