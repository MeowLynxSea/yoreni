import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMemoryBackfill } from "@/hooks/useMemoryBackfill";
import { setMemoryBackfillPaused } from "@/lib/api";
import { formatTurnLatency } from "@/lib/format-time";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { WidgetCard, WidgetPill } from "./WidgetCard";

const numberFormat = new Intl.NumberFormat();

export function MemoryBackfillWidget() {
  const { t } = useTranslation();
  const { client } = useClient();
  const state = useMemoryBackfill();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  // The card owns its own visibility: no work means nothing to show.
  if (!state.active) return null;

  const paused = state.status === "paused";
  const done = state.done ?? 0;
  const total = state.total ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const eta = !paused && state.eta_ms ? formatTurnLatency(state.eta_ms) : null;

  const toggle = async () => {
    setBusy(true);
    setFailed(false);
    try {
      await setMemoryBackfillPaused(client, !paused);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <WidgetCard
      title={t("home.widgets.memoryBackfill.title")}
      badge={(
        <WidgetPill tone={paused ? "muted" : "active"}>
          {paused
            ? t("home.widgets.memoryBackfill.paused")
            : t("home.widgets.memoryBackfill.running")}
        </WidgetPill>
      )}
    >
      <p className="text-[14px] leading-6 text-foreground/90">
        {t("home.widgets.memoryBackfill.progress", {
          done: numberFormat.format(done),
          total: numberFormat.format(total),
        })}
      </p>
      <div
        role="progressbar"
        aria-label={t("home.widgets.memoryBackfill.title")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-foreground/10"
      >
        <div
          className={cn(
            "h-full rounded-full bg-primary/70 transition-[width] duration-500 ease-out motion-reduce:transition-none",
            paused && "bg-foreground/25",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-3 flex min-h-8 items-center justify-between gap-3">
        <span className="text-[12px] leading-5 text-muted-foreground">
          {failed
            ? t("home.widgets.memoryBackfill.failed")
            : eta
              ? t("home.widgets.memoryBackfill.eta", { time: eta })
              : ""}
        </span>
        <button
          type="button"
          onClick={() => void toggle()}
          disabled={busy}
          className="quiet-control rounded-full px-3 py-1.5 text-[12px] leading-5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none"
        >
          {paused
            ? t("home.widgets.memoryBackfill.resume")
            : t("home.widgets.memoryBackfill.pause")}
        </button>
      </div>
    </WidgetCard>
  );
}
