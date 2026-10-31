import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import {
  disconnectCredential,
  fetchCredentials,
} from "@/lib/api";
import type {
  ConnectorSummary,
  CredentialAccountSummary,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type Tab = "all" | "connected" | "login" | "api_key";

const TABS: Tab[] = ["all", "connected", "login", "api_key"];

function AccountMark({ name }: { name: string }) {
  const label = name.trim().charAt(0).toUpperCase() || "·";
  return (
    <span
      aria-hidden
      className="grid h-9 w-9 shrink-0 place-items-center rounded-[0.65rem] bg-foreground/12 text-[13px] font-semibold text-foreground/75"
    >
      {label}
    </span>
  );
}

function connectorMethod(connector: ConnectorSummary | undefined, account: CredentialAccountSummary): string {
  if (account.auth_method) return account.auth_method;
  return connector?.auth_methods[0] ?? "";
}

export function AccountsSettings() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [accounts, setAccounts] = useState<CredentialAccountSummary[]>([]);
  const [connectors, setConnectors] = useState<ConnectorSummary[]>([]);
  const [tab, setTab] = useState<Tab>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const state = await fetchCredentials(getToken());
      setAccounts(state.accounts);
      setConnectors(state.connectors);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const connectorById = useMemo(
    () => new Map(connectors.map((connector) => [connector.id, connector])),
    [connectors],
  );

  const removeAccount = useCallback(
    async (account: CredentialAccountSummary) => {
      setBusy(account.id);
      setError(null);
      try {
        await disconnectCredential(client, account.id);
        await refresh();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setBusy(null);
      }
    },
    [client, refresh],
  );

  const visible = useMemo(() => {
    return accounts.filter((account) => {
      if (tab === "all" || tab === "connected") return true;
      const method = connectorMethod(connectorById.get(account.connector_id), account);
      if (tab === "login") return method === "basic";
      return method === "api_key" || method === "bearer";
    });
  }, [accounts, connectorById, tab]);

  return (
    <div className="settings-stack mx-auto w-full max-w-[42rem]">
      <div className="px-1">
        <h2 className="text-[19px] font-medium text-foreground/95">{t("accounts.title")}</h2>
        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
          {t("accounts.description")}
        </p>
      </div>

      <div className="mt-1 flex gap-1 rounded-full bg-muted/60 p-1">
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setTab(item)}
            className={cn(
              "flex-1 rounded-full px-3 py-2 text-[13px] transition-colors duration-150 motion-reduce:transition-none",
              tab === item
                ? "bg-foreground text-background shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(`accounts.tabs.${item}`)}
          </button>
        ))}
      </div>

      {error ? (
        <p role="alert" className="rounded-control bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 px-1 py-8 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("accounts.loading")}
        </div>
      ) : visible.length === 0 ? (
        <div className="settings-sheet px-5 py-10 text-center">
          <p className="text-[14px] text-foreground/80">{t("accounts.empty.title")}</p>
          <p className="mx-auto mt-1 max-w-[26rem] text-[12.5px] leading-5 text-muted-foreground">
            {t("accounts.empty.description")}
          </p>
        </div>
      ) : (
        <ul className="settings-sheet divide-y divide-[color:var(--app-hairline)] overflow-hidden">
          {visible.map((account) => {
            const connector = connectorById.get(account.connector_id);
            const name = t(`accounts.services.${account.connector_id}.name`, {
              defaultValue: connector?.name ?? account.connector_id,
            });
            return (
              <li key={account.id} className="flex items-center gap-3 px-4 py-3.5">
                <AccountMark name={name} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] text-foreground/95">{account.label}</p>
                  <p className="truncate text-[12.5px] text-muted-foreground">
                    {name} · {t(`accounts.status.${account.status}`)}
                  </p>
                </div>
                <button
                  type="button"
                  className="shrink-0 text-[12.5px] text-muted-foreground underline-offset-2 hover:text-destructive hover:underline disabled:opacity-50"
                  disabled={busy === account.id}
                  onClick={() => { void removeAccount(account); }}
                >
                  {busy === account.id ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    t("accounts.disconnect")
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
