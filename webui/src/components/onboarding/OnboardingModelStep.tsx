import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ArrowRight, Check, CircleAlert, Loader2, PlugZap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  createModelConfiguration,
  createProviderSettings,
  fetchProviderModels,
  fetchSettings,
  updateAgentSettings,
  updateModelConfiguration,
  updateProviderSettings,
} from "@/lib/api";
import type { ProviderModelsPayload, SettingsPayload } from "@/lib/types";
import type { YoreniClient } from "@/lib/yoreni-client";

const CUSTOM_PROVIDER = "__custom__";

type ModelOption = ProviderModelsPayload["models"][number];

function positiveNumber(value: number | null | undefined): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value
    : undefined;
}

function presetNameFor(model: string, payload: SettingsPayload): string {
  const base = (model.trim().split("/").filter(Boolean).at(-1) ?? "model")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "model";
  const existing = new Set(
    payload.model_presets
      .filter((preset) => !preset.is_default)
      .map((preset) => preset.name.toLowerCase()),
  );
  if (!existing.has(base)) return base;
  for (let index = 2; ; index += 1) {
    const candidate = `${base.slice(0, 45)}-${index}`;
    if (!existing.has(candidate)) return candidate;
  }
}

export function OnboardingModelStep({
  client,
  apiToken,
  online,
  onBack,
  onDone,
}: {
  client: YoreniClient;
  apiToken: string;
  online: boolean;
  onBack: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [provider, setProvider] = useState("");
  const [customName, setCustomName] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [savedBase, setSavedBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [catalogStatus, setCatalogStatus] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tested, setTested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const activePreset = useRef<string | null>(null);
  const initialModel = useRef("");

  const providerOptions = useMemo(
    () => (settings?.providers ?? [])
      .filter((row) => row.auth_type !== "oauth" || row.configured)
      .sort((left, right) => Number(right.configured) - Number(left.configured)),
    [settings],
  );
  const row = settings?.providers.find((entry) => entry.name === provider) ?? null;
  const custom = provider === CUSTOM_PROVIDER;

  const failure = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : String(reason));

  const applySettings = (payload: SettingsPayload, providerName: string) => {
    setSettings(payload);
    const nextRow = payload.providers.find((entry) => entry.name === providerName);
    const base = nextRow?.api_base ?? nextRow?.default_api_base ?? "";
    setApiBase(base);
    setSavedBase(base);
  };

  const loadModels = async (providerName: string) => {
    setCatalogStatus(null);
    try {
      const catalog = await fetchProviderModels(apiToken, providerName, "", { refresh: true });
      setModels(catalog.models);
      setCatalogStatus(catalog.status);
      const reachable = catalog.status === "available" || catalog.status === "unsupported";
      setTested(reachable);
      if (reachable && catalog.models.length === 1) {
        setModel((current) => current || catalog.models[0].id);
      }
    } catch (reason) {
      setTested(false);
      setCatalogStatus("error");
      failure(reason);
    }
  };

  useEffect(() => {
    let alive = true;
    fetchSettings(apiToken)
      .then((payload) => {
        if (!alive) return;
        setSettings(payload);
        const preset = payload.model_presets.find((entry) => entry.active && !entry.is_default)
          ?? payload.model_presets.find((entry) => entry.name === payload.agent.model_preset && !entry.is_default)
          ?? null;
        activePreset.current = preset?.name ?? null;
        const resolved = preset
          ? (preset.provider === "auto" ? payload.agent.resolved_provider : preset.provider)
          : (payload.agent.resolved_provider ?? payload.agent.provider);
        const initialProvider = resolved && resolved !== "auto" ? resolved : "";
        const initialRow = payload.providers.find((entry) => entry.name === initialProvider);
        setProvider(initialProvider);
        const base = initialRow?.api_base ?? initialRow?.default_api_base ?? "";
        setApiBase(base);
        setSavedBase(base);
        initialModel.current = preset?.model ?? payload.agent.model;
        if (initialProvider && initialRow?.configured) {
          setModel(preset?.model ?? payload.agent.model);
          setTested(true);
          void loadModels(initialProvider);
        }
      })
      .catch(failure);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  const chooseProvider = (value: string) => {
    setProvider(value);
    setError(null);
    setTested(false);
    setModels([]);
    setCatalogStatus(null);
    setModel("");
    setApiKey("");
    initialModel.current = "";
    const nextRow = settings?.providers.find((entry) => entry.name === value);
    const base = value === CUSTOM_PROVIDER ? "" : nextRow?.api_base ?? nextRow?.default_api_base ?? "";
    setApiBase(base);
    setSavedBase(base);
    if (nextRow?.configured) {
      setTested(true);
      void loadModels(value);
    }
  };

  const connect = async (): Promise<string | null> => {
    if (!settings) return null;
    setError(null);
    if (custom) {
      if (!customName.trim()) {
        setError(t("onboarding.model.providerNameRequired"));
        return null;
      }
      if (!apiBase.trim()) {
        setError(t("onboarding.model.apiBaseRequired"));
        return null;
      }
      const payload = await createProviderSettings(client, {
        name: customName.trim(),
        apiBase: apiBase.trim(),
        ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
      });
      const created = payload.created_provider;
      if (!created) throw new Error("Provider was not created");
      applySettings(payload, created);
      setProvider(created);
      await loadModels(created);
      return created;
    }
    if (!row) {
      setError(t("onboarding.model.providerRequired"));
      return null;
    }
    const changedBase = apiBase.trim() && apiBase.trim() !== savedBase;
    if (apiKey.trim() || changedBase) {
      const payload = await updateProviderSettings(client, {
        provider,
        ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
        ...(changedBase ? { apiBase: apiBase.trim() } : {}),
      });
      applySettings(payload, provider);
    }
    await loadModels(provider);
    return provider;
  };

  const runConnect = async () => {
    if (!online || connecting) return;
    setConnecting(true);
    try {
      await connect();
    } catch (reason) {
      failure(reason);
    } finally {
      setConnecting(false);
    }
  };

  const saveAndContinue = async () => {
    if (!settings || saving || !online) return;
    if (!provider) {
      setError(t("onboarding.model.providerRequired"));
      return;
    }
    if (!model.trim()) {
      setError(t("onboarding.model.modelRequired"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let providerName = provider;
      const changedBase = apiBase.trim() && apiBase.trim() !== savedBase;
      if (custom || !row?.configured || apiKey.trim() || changedBase) {
        const connected = await connect();
        if (!connected) return;
        providerName = connected;
      }
      const catalogModel = models.find((entry) => entry.id === model.trim());
      const applyCatalog = !activePreset.current || model.trim() !== initialModel.current;
      const contextWindowTokens = applyCatalog
        ? positiveNumber(catalogModel?.context_window)
        : undefined;
      const maxTokens = applyCatalog
        ? positiveNumber(catalogModel?.max_output_tokens)
        : undefined;
      if (activePreset.current) {
        const payload = await updateModelConfiguration(client, {
          name: activePreset.current,
          provider: providerName,
          model: model.trim(),
          ...(contextWindowTokens ? { contextWindowTokens } : {}),
          ...(maxTokens ? { maxTokens } : {}),
        });
        applySettings(payload, providerName);
      } else {
        const payload = await createModelConfiguration(client, {
          name: presetNameFor(model, settings),
          provider: providerName,
          model: model.trim(),
          ...(contextWindowTokens ? { contextWindowTokens } : {}),
          ...(maxTokens ? { maxTokens } : {}),
        });
        const created = payload.created_model_preset ?? null;
        activePreset.current = created;
        // A legacy default keeps its own selection; make the new preset primary.
        if (created && payload.agent.model_preset !== created) {
          const activated = await updateAgentSettings(client, { modelPreset: created });
          applySettings(activated, providerName);
        } else {
          applySettings(payload, providerName);
        }
      }
      onDone();
    } catch (reason) {
      failure(reason);
    } finally {
      setSaving(false);
    }
  };

  if (!settings) {
    if (error) {
      return (
        <div className="flex min-h-[16rem] flex-col items-center justify-center gap-3 text-center">
          <p role="alert" className="max-w-sm text-[13px] leading-6 text-destructive">{error}</p>
          <Button variant="outline" onClick={() => setReload((value) => value + 1)} className="h-10 rounded-full px-5">
            {t("common.retry")}
          </Button>
        </div>
      );
    }
    return (
      <div className="flex min-h-[16rem] items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }

  const needsKey = custom || (Boolean(row) && !row?.configured && row?.api_key_required !== false);

  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70">
        {t("onboarding.model.eyebrow")}
      </p>
      <h1 className="display-serif mt-3 text-[clamp(1.9rem,4.8vw,2.7rem)] leading-[1.12] text-foreground/95">
        {t("onboarding.model.title")}
      </h1>
      <p className="mt-3 max-w-lg text-[13.5px] leading-6 text-muted-foreground">
        {t("onboarding.model.body")}
      </p>

      <div className="mt-7 space-y-4 rounded-[1.75rem] border border-white/55 bg-white/40 p-5 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.04] sm:p-6">
        <div>
          <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-provider">
            {t("onboarding.model.provider")}
          </label>
          <Select value={provider} onValueChange={chooseProvider}>
            <SelectTrigger
              id="onboarding-provider"
              className="h-12 w-full justify-between rounded-full border-white/60 bg-white/60 text-[14px] font-normal dark:border-white/10 dark:bg-white/[0.06]"
            >
              <SelectValue placeholder={t("onboarding.model.providerPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={CUSTOM_PROVIDER}>{t("onboarding.model.customProvider")}</SelectItem>
              {providerOptions.map((entry) => (
                <SelectItem key={entry.name} value={entry.name}>
                  <span className="flex items-center gap-2">
                    <span>{entry.label}</span>
                    {entry.configured ? (
                      <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
                    ) : null}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {custom ? (
          <div className="onboarding-field-enter">
            <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-provider-name">
              {t("onboarding.model.providerName")}
            </label>
            <Input
              id="onboarding-provider-name"
              value={customName}
              onChange={(event) => setCustomName(event.target.value)}
              placeholder={t("onboarding.model.providerNamePlaceholder")}
              maxLength={80}
              className="h-12 rounded-full border-white/60 bg-white/60 px-5 text-[14px] dark:border-white/10 dark:bg-white/[0.06]"
            />
          </div>
        ) : null}

        {provider ? (
          <div className="onboarding-field-enter space-y-4">
            {custom || row?.api_base !== undefined ? (
              <div>
                <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-api-base">
                  {t("onboarding.model.apiBase")}
                </label>
                <Input
                  id="onboarding-api-base"
                  value={apiBase}
                  onChange={(event) => setApiBase(event.target.value)}
                  placeholder="https://api.example.com/v1"
                  inputMode="url"
                  autoComplete="url"
                  className="h-12 rounded-full border-white/60 bg-white/60 px-5 font-mono text-[13px] dark:border-white/10 dark:bg-white/[0.06]"
                />
              </div>
            ) : null}
            {needsKey ? (
              <div>
                <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-api-key">
                  {t("onboarding.model.apiKey")}
                </label>
                <Input
                  id="onboarding-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={row?.configured ? t("onboarding.model.apiKeyKeep") : t("onboarding.model.apiKeyPlaceholder")}
                  autoComplete="off"
                  className="h-12 rounded-full border-white/60 bg-white/60 px-5 text-[14px] dark:border-white/10 dark:bg-white/[0.06]"
                />
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant="outline"
                onClick={() => void runConnect()}
                disabled={!online || connecting}
                className="h-10 rounded-full border-foreground/15 px-5 text-[13px]"
              >
                {connecting
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  : <PlugZap className="h-3.5 w-3.5" aria-hidden />}
                {t("onboarding.model.test")}
              </Button>
              {tested ? (
                <span className="inline-flex items-center gap-1.5 text-[12.5px] text-emerald-700 dark:text-emerald-400">
                  <Check className="h-3.5 w-3.5" aria-hidden />
                  {catalogStatus === "unsupported" || models.length === 0
                    ? t("onboarding.model.reachable")
                    : t("onboarding.model.connected", { count: models.length })}
                </span>
              ) : catalogStatus === "error" ? (
                <span className="inline-flex items-center gap-1.5 text-[12.5px] text-destructive">
                  <CircleAlert className="h-3.5 w-3.5" aria-hidden />
                  {t("onboarding.model.connectFailed")}
                </span>
              ) : null}
            </div>

            <div>
              <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-model">
                {t("onboarding.model.model")}
              </label>
              <Input
                id="onboarding-model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={t("onboarding.model.modelPlaceholder")}
                list="onboarding-model-options"
                autoComplete="off"
                className="h-12 rounded-full border-white/60 bg-white/60 px-5 font-mono text-[13px] dark:border-white/10 dark:bg-white/[0.06]"
              />
              <datalist id="onboarding-model-options">
                {models.map((entry) => (
                  <option key={entry.id} value={entry.id}>{entry.label ?? entry.id}</option>
                ))}
              </datalist>
              {models.length > 0 ? (
                <p className="mt-2 text-[11.5px] text-muted-foreground/70">
                  {t("onboarding.model.modelCount", { count: models.length })}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="rounded-xl bg-destructive/8 px-3 py-2 text-[12.5px] leading-5 text-destructive">
            {error}
          </p>
        ) : null}
      </div>

      <div className="mt-7 flex items-center justify-between gap-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t("onboarding.back")}
        </button>
        <Button
          onClick={() => void saveAndContinue()}
          disabled={!online || saving || !provider || !model.trim()}
          className="h-11 rounded-full px-6"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ArrowRight className="h-4 w-4" aria-hidden />}
          <span>{t("onboarding.continue")}</span>
        </Button>
      </div>
    </div>
  );
}
