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
    <div className="z-10 shrink-0 border-b border-border bg-muted/40 px-4 py-2">
      {runs.map((run) => (
        <div key={run.id} className="flex items-center justify-between gap-3 text-sm">
          <span>{t("recovery.interrupted")} <code className="text-xs">{run.id.slice(0, 8)}</code></span>
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
        </div>
      ))}
      {error && <p role="alert" className="text-sm text-destructive">{t("recovery.actionFailed")}</p>}
    </div>
  );
}
