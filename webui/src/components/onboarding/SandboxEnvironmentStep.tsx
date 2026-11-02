import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Check, Download, Loader2, Play, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchSandboxHost, openPodroidInstall, startPodroidHost } from "@/lib/api";
import type { PodroidHost } from "@/lib/types";
import type { YoreniClient } from "@/lib/yoreni-client";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;

/**
 * First-run step on a phone: Yoreni's Linux environment is a separate app, so
 * the owner is walked from "not installed" to "VM answering" before anything
 * else can be prepared.
 */
export function SandboxEnvironmentStep({
  client,
  apiToken,
  online,
  onReady,
}: {
  client: YoreniClient;
  apiToken: string;
  online: boolean;
  onReady: () => void;
}) {
  const { t } = useTranslation();
  const [host, setHost] = useState<PodroidHost | null>(null);
  const [installing, setInstalling] = useState(false);
  const [starting, setStarting] = useState(false);
  // The VM boots for a minute after the broadcast, so the request outlives the
  // call that made it.
  const [requested, setRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = useRef(false);
  const readyCallback = useRef(onReady);
  readyCallback.current = onReady;

  // Poll instead of trusting one answer: the owner leaves the app to install
  // Podroid and comes back, and the VM boots for a while after it is asked to.
  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const next = await fetchSandboxHost(apiToken);
        if (live && next.mode === "podroid") setHost(next);
      } catch {
        // Keep the last known state; the next tick retries.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [apiToken]);

  useEffect(() => {
    if (!host?.vm_running || ready.current) return;
    ready.current = true;
    readyCallback.current();
  }, [host?.vm_running]);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const next = await fetchSandboxHost(apiToken);
      if (next.mode === "podroid") setHost(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [apiToken]);

  const install = async () => {
    if (installing) return;
    setInstalling(true);
    setError(null);
    try {
      const next = await openPodroidInstall(client);
      if (next.mode === "podroid") setHost(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setInstalling(false);
    }
  };

  const start = async () => {
    if (starting) return;
    setStarting(true);
    setError(null);
    try {
      const next = await startPodroidHost(client);
      setRequested(true);
      if (next.mode === "podroid") setHost(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  const installed = host?.installed ?? false;
  const running = host?.vm_running ?? false;
  const booting = starting || (requested && !running);

  const view = (() => {
    if (running) {
      return {
        icon: <Check className="h-4 w-4" strokeWidth={2.6} aria-hidden />,
        title: t("onboarding.host.readyTitle"),
        body: t("onboarding.host.readyBody"),
      };
    }
    if (!host) {
      return {
        icon: <Loader2 className="h-4 w-4 animate-spin" aria-hidden />,
        title: t("onboarding.host.checking"),
        body: t("onboarding.host.checkingBody"),
      };
    }
    if (!installed) {
      return {
        icon: <Download className="h-4 w-4" strokeWidth={2.1} aria-hidden />,
        title: t("onboarding.host.missingTitle"),
        body: t("onboarding.host.missingBody"),
      };
    }
    if (booting) {
      return {
        icon: <Loader2 className="h-4 w-4 animate-spin" aria-hidden />,
        title: t("onboarding.host.starting"),
        body: t("onboarding.host.startingBody"),
      };
    }
    return {
      icon: <Play className="h-4 w-4" strokeWidth={2.1} aria-hidden />,
      title: t("onboarding.host.stoppedTitle"),
      body: t("onboarding.host.stoppedBody"),
    };
  })();

  const action = (() => {
    if (running || !host) return null;
    if (!installed) {
      return (
        <Button
          type="button"
          onClick={() => void install()}
          disabled={installing || !online}
          className="h-11 rounded-full px-5"
        >
          {installing
            ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            : <Download className="h-4 w-4" aria-hidden />}
          <span>{t("onboarding.host.install")}</span>
        </Button>
      );
    }
    if (!booting) {
      return (
        <Button
          type="button"
          onClick={() => void start()}
          disabled={starting || !online}
          className="h-11 rounded-full px-5"
        >
          <Play className="h-4 w-4" aria-hidden />
          <span>{t("onboarding.host.start")}</span>
        </Button>
      );
    }
    return null;
  })();

  return (
    <section className="onboarding-stage mx-auto w-full max-w-xl">
      <div className="onboarding-stage-stack">
        <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70" style={{ "--onboarding-index": 0 } as CSSProperties}>
          {t("onboarding.host.eyebrow")}
        </p>
        <h1 className="display-serif mt-4 text-[clamp(2rem,5.6vw,3rem)] leading-[1.1] text-foreground/95" style={{ "--onboarding-index": 1 } as CSSProperties}>
          {t("onboarding.host.question")}
        </h1>
        <p className="mt-4 max-w-md text-[14px] leading-7 text-muted-foreground" style={{ "--onboarding-index": 2 } as CSSProperties}>
          {t("onboarding.host.hint")}
        </p>
      </div>

      {error ? (
        <div
          role="alert"
          className="onboarding-field-enter mt-6 rounded-2xl border border-destructive/20 bg-destructive/8 px-4 py-3 text-[13px] leading-5 text-destructive"
        >
          {error}
        </div>
      ) : null}

      <div className="onboarding-stage mt-7 rounded-3xl border border-foreground/10 bg-white/55 p-5 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]">
        <div className="flex items-start gap-3.5">
          <span
            className={cn(
              "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-2xl transition-colors",
              running ? "bg-foreground text-background" : "bg-foreground/8 text-foreground/75",
            )}
          >
            {view.icon}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[14px] font-medium tracking-[-0.01em] text-foreground/90">
              {view.title}
            </p>
            <p className="mt-1 text-[12.5px] leading-5 text-muted-foreground">
              {view.body}
            </p>
            {installed && host?.version ? (
              <p className="mt-2 text-[11px] tabular-nums text-muted-foreground/60">
                Podroid {host.version}
              </p>
            ) : null}
          </div>
        </div>

        {action || (host && !running) ? (
          <div className="mt-4 flex items-center gap-4">
            {action}
            {!installed ? (
              <button
                type="button"
                onClick={() => void refresh()}
                className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                {t("onboarding.host.checkAgain")}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <p className="mt-6 text-[11.5px] leading-5 text-muted-foreground/60">
        {t("onboarding.host.footnote")}
      </p>
    </section>
  );
}
