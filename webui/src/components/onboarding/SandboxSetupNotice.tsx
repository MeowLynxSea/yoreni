import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, RefreshCw } from "lucide-react";

import type { SandboxSetupState } from "@/lib/types";
import { cn } from "@/lib/utils";

const STAGE_KEYS: Record<SandboxSetupState["stage"], string> = {
  starting: "onboarding.sandbox.stage.starting",
  image: "onboarding.sandbox.stage.image",
  configuring: "onboarding.sandbox.stage.configuring",
  packages: "onboarding.sandbox.stage.packages",
  browser: "onboarding.sandbox.stage.browser",
  services: "onboarding.sandbox.stage.services",
  ready: "onboarding.sandbox.stage.ready",
};

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1000 && index < units.length - 1) {
    value /= 1000;
    index += 1;
  }
  return `${value >= 100 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

/**
 * Persistent first-run notice: the sandbox VM is being prepared for the whole
 * onboarding, and finishing waits for it.
 */
export function SandboxSetupNotice({
  state,
  onRetry,
  retrying,
}: {
  state: SandboxSetupState;
  onRetry: () => void;
  retrying: boolean;
}) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (state.state !== "ready") {
      setHidden(false);
      if (timer.current !== null) window.clearTimeout(timer.current);
      return;
    }
    timer.current = window.setTimeout(() => setHidden(true), 1600);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [state.state]);

  if (state.state === "unsupported" || hidden) return null;

  const failed = state.state === "failed";
  const ready = state.state === "ready";
  const indeterminate = !failed && !ready && state.percent === null;
  const percent = ready ? 100 : state.percent;

  const detail = (() => {
    if (failed) return state.error || t("onboarding.sandbox.failed");
    if (ready) return t("onboarding.sandbox.ready");
    const parts: string[] = [];
    if (state.downloaded_bytes !== null && state.total_bytes) {
      parts.push(`${formatBytes(state.downloaded_bytes)} / ${formatBytes(state.total_bytes)}`);
    }
    if (state.rate_bytes_per_s) {
      parts.push(`${formatBytes(state.rate_bytes_per_s)}/s`);
    }
    if (state.eta_seconds !== null && state.eta_seconds > 0) {
      parts.push(t("onboarding.sandbox.eta", { time: formatDuration(state.eta_seconds) }));
    } else if (parts.length === 0) {
      parts.push(t("onboarding.sandbox.elapsed", { time: formatDuration(state.elapsed_s) }));
    }
    return parts.join(" · ");
  })();

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "sandbox-setup rounded-2xl border border-border/60 bg-background/85 px-4 py-3 shadow-[0_18px_40px_-24px_rgba(20,32,48,0.55)] backdrop-blur-xl fixed bottom-[max(1rem,env(safe-area-inset-bottom))] left-1/2 z-20 w-[min(30rem,calc(100%-2rem))] -translate-x-1/2",
        failed && "sandbox-setup--failed",
        ready && "sandbox-setup--ready",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="sandbox-setup__dot mt-1.5" aria-hidden>
          {ready ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <p className="truncate text-[13px] font-medium tracking-[-0.01em] text-foreground/90">
              {failed ? t("onboarding.sandbox.failedTitle") : t(STAGE_KEYS[state.stage])}
            </p>
            {percent !== null && !failed ? (
              <span className="shrink-0 tabular-nums text-[12px] text-muted-foreground">
                {Math.round(percent)}%
              </span>
            ) : null}
          </div>
          <div
            className={cn("sandbox-setup__track mt-2", indeterminate && "sandbox-setup__track--indeterminate")}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent ?? undefined}
            aria-valuetext={indeterminate ? t("onboarding.sandbox.preparing") : detail}
          >
            <span
              className="sandbox-setup__fill"
              style={percent === null ? undefined : { width: `${percent}%` }}
            />
          </div>
          <p
            className={cn(
              "mt-2 truncate text-[11.5px] leading-4 text-muted-foreground",
              failed && "whitespace-normal break-words text-destructive/90",
            )}
          >
            {detail}
          </p>
        </div>
        {failed ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/70 px-2.5 py-1 text-[11.5px] text-foreground/80 transition-colors hover:bg-foreground/5 disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3 w-3", retrying && "animate-spin")} aria-hidden />
            {t("onboarding.sandbox.retry")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
