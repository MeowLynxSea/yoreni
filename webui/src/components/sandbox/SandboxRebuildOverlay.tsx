import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";

import { SANDBOX_STAGE_KEYS, formatDuration } from "@/components/onboarding/SandboxSetupNotice";
import { Button } from "@/components/ui/button";
import { fetchSandboxStatus, rebuildSandbox } from "@/lib/api";
import { markSandboxRebuild, onSandboxRebuild } from "@/lib/sandbox-rebuild";
import type { SandboxSetupState } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

const POLL_MS = 1500;
const DONE_MS = 1600;

/**
 * Blocks the whole interface while the VM is being rebuilt. A rebuild replaces
 * the virtual machine the browser and every file tool run on, so nothing the
 * user could do in the meantime would work.
 */
export function SandboxRebuildOverlay({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [active, setActive] = useState(false);
  const [setup, setSetup] = useState<SandboxSetupState | null>(null);
  const [done, setDone] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const hideTimer = useRef<number | null>(null);

  useEffect(() => onSandboxRebuild((requested) => {
    if (!requested) return;
    setActive(true);
    setDone(false);
    setSetup(null);
  }), []);

  useEffect(() => {
    if (!enabled || !active) return;
    let live = true;
    const poll = async () => {
      const token = getToken();
      if (!token) return;
      try {
        const status = await fetchSandboxStatus(token);
        if (!live) return;
        setSetup(status.setup);
        if (status.setup.state === "running") return;
        if (status.setup.state === "failed") return;
        setDone(true);
        hideTimer.current = window.setTimeout(() => {
          setActive(false);
          setDone(false);
          markSandboxRebuild(false);
        }, DONE_MS);
      } catch {
        // The next poll retries; a dropped request is not a failed rebuild.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    };
  }, [active, enabled, getToken]);

  const retry = useCallback(async () => {
    setRetrying(true);
    try {
      await rebuildSandbox(client);
    } catch {
      // The overlay keeps the error state and offers the button again.
    } finally {
      setRetrying(false);
    }
  }, [client]);

  if (!active) return null;

  const failed = setup?.state === "failed";
  const percent = done ? 100 : setup?.percent ?? null;
  const indeterminate = !failed && !done && percent === null;
  const detail = (() => {
    if (done) return t("onboarding.sandbox.ready");
    if (failed) return setup?.error || t("onboarding.sandbox.failed");
    if (setup && setup.eta_seconds !== null && setup.eta_seconds > 0) {
      return t("onboarding.sandbox.eta", { time: formatDuration(setup.eta_seconds) });
    }
    return t("onboarding.sandbox.elapsed", { time: formatDuration(setup?.elapsed_s ?? 0) });
  })();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-busy={!failed && !done}
      className="fixed inset-0 z-50 grid place-items-center bg-background/80 px-6 backdrop-blur-md"
    >
      <div className="w-[min(26rem,100%)] rounded-3xl border border-border/60 bg-background/95 p-6 text-center shadow-[0_30px_80px_-40px_rgba(20,32,48,0.7)]">
        <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70">
          {t("settings.sandbox.title")}
        </p>
        <h2 className="display-serif mt-3 text-[clamp(1.5rem,4vw,1.9rem)] leading-tight text-foreground/95">
          {failed
            ? t("onboarding.sandbox.failedTitle")
            : done
              ? t("onboarding.sandbox.ready")
              : setup
                ? t(SANDBOX_STAGE_KEYS[setup.stage])
                : t("onboarding.sandbox.stage.starting")}
        </h2>
        <div
          className="mt-5 h-1.5 overflow-hidden rounded-full bg-foreground/10"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
          aria-valuetext={detail}
        >
          <span
            className={cn(
              "block h-full rounded-full bg-foreground/70 transition-[width] duration-500",
              indeterminate && "w-full animate-pulse",
            )}
            style={percent === null ? undefined : { width: `${percent}%` }}
          />
        </div>
        <p
          className={cn(
            "mt-3 text-[12px] leading-5 text-muted-foreground",
            failed && "whitespace-normal break-words text-destructive/90",
          )}
        >
          {detail}
        </p>
        {failed ? (
          <Button
            type="button"
            onClick={() => void retry()}
            disabled={retrying}
            className="mt-5 h-10 rounded-full px-5"
          >
            {retrying
              ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              : <RefreshCw className="h-4 w-4" aria-hidden />}
            <span>{t("onboarding.sandbox.retry")}</span>
          </Button>
        ) : null}
      </div>
    </div>
  );
}
