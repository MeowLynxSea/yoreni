import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchAutomations } from "@/lib/api";
import { relativeTime } from "@/lib/format-time";
import type { AutomationJob } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

import { WidgetCard, WidgetPill } from "./WidgetCard";

function upcoming(jobs: AutomationJob[]): AutomationJob[] {
  return jobs
    .filter((job) => job.enabled && job.state.next_run_at_ms)
    .sort((left, right) => (left.state.next_run_at_ms ?? 0) - (right.state.next_run_at_ms ?? 0))
    .slice(0, 3);
}

export function AutomationsWidget() {
  const { t } = useTranslation();
  const { getToken } = useClient();
  const [jobs, setJobs] = useState<AutomationJob[]>([]);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      void fetchAutomations(getToken())
        .then((payload) => { if (!cancelled) setJobs(payload.jobs); })
        .catch(() => {});
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => { cancelled = true; window.removeEventListener("focus", refresh); };
  }, [getToken]);

  const scheduled = jobs.filter((job) => (
    job.enabled && job.kind !== "local_trigger" && job.payload.kind !== "system_event"
  ));
  const next = upcoming(scheduled);
  const title = t("home.widgets.automations.title");

  if (!scheduled.length) return null;

  return (
    <WidgetCard
      title={title}
      badge={<WidgetPill tone="muted">{scheduled.length}</WidgetPill>}
    >
      {next.length ? (
        <ul className="space-y-2.5">
          {next.map((job) => (
            <li key={job.id} className="flex items-baseline justify-between gap-3">
              <span className="truncate text-[13px] leading-6 text-foreground/90">{job.name}</span>
              <span className="shrink-0 text-[12px] leading-6 text-muted-foreground">
                {relativeTime(job.state.next_run_at_ms)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {scheduled.length > next.length ? (
        <p className="mt-3 text-[12px] leading-5 text-muted-foreground">
          {t("home.widgets.automations.more", { count: scheduled.length - next.length })}
        </p>
      ) : null}
    </WidgetCard>
  );
}
