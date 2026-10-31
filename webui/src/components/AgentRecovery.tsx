import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPersonalAgent, type PersonalAgentInfo } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import { Button } from "@/components/ui/button";

export function AgentRecovery() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [runs, setRuns] = useState<PersonalAgentInfo["pending_runs"]>([]);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    const info = await fetchPersonalAgent(getToken());
    setRuns(info.pending_runs);
  }, [getToken]);

  useEffect(() => {
    let disposed = false;
    const update = () => {
      void fetchPersonalAgent(getToken()).then((info) => {
        if (!disposed) setRuns(info.pending_runs);
      }).catch(() => {});
    };
    const offReady = client.onReady(update);
    const offUpdate = client.onEvent((frame) => { if (frame.event === "turn_end") update(); });
    update();
    return () => {
      disposed = true;
      offReady();
      offUpdate();
    };
  }, [client, getToken]);

  if (!runs.length) return null;
  return (
    <div className="z-10 mx-auto mt-2 flex w-fit max-w-[calc(100vw-2rem)] shrink-0 flex-col gap-1.5 rounded-[1.25rem] border border-white/55 bg-white/45 px-4 py-2 backdrop-blur-md dark:border-white/10 dark:bg-white/[0.05]">
      {runs.map((run) => (
        <div key={run.id} className="flex items-center justify-between gap-3 text-sm">
          <span className="truncate">{t("recovery.interrupted")} <code className="text-xs text-muted-foreground">{run.id.slice(0, 8)}</code></span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== null}
              onClick={() => {
                setBusy(run.id);
                setError(false);
                void client.requestMutation("agent.resume", { run_id: run.id })
                  .then(() => setRuns((current) => current.filter((item) => item.id !== run.id)))
                  .catch(() => setError(true))
                  .finally(() => { setBusy(null); void refresh().catch(() => {}); });
              }}
            >{t("recovery.continue")}</Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              disabled={busy !== null}
              onClick={() => {
                setBusy(run.id);
                setError(false);
                void client.requestMutation("agent.dismiss", { run_id: run.id })
                  .then(() => setRuns((current) => current.filter((item) => item.id !== run.id)))
                  .catch(() => setError(true))
                  .finally(() => { setBusy(null); void refresh().catch(() => {}); });
              }}
            >{t("recovery.dismiss")}</Button>
          </div>
        </div>
      ))}
      {error && <p role="alert" className="text-sm text-destructive">{t("recovery.actionFailed")}</p>}
    </div>
  );
}
