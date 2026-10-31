import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Check, CircleAlert, Loader2, PlugZap, Sparkles } from "lucide-react";

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
  fetchSettings,
  testEmbeddingEndpoint,
  updateMemorySettings,
} from "@/lib/api";
import { memoryFormFromPayload } from "@/components/settings/capabilities/MemorySettings";
import type { SettingsPayload } from "@/lib/types";
import type { YoreniClient } from "@/lib/yoreni-client";

const CUSTOM_ENDPOINT = "__custom__";

export function OnboardingEmbeddingStep({
  client,
  apiToken,
  online,
  finishLabel,
  busy,
  onBack,
  onDone,
}: {
  client: YoreniClient;
  apiToken: string;
  online: boolean;
  finishLabel: string;
  busy: boolean;
  onBack: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [provider, setProvider] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [dimensions, setDimensions] = useState("");
  const [multimodal, setMultimodal] = useState(false);
  const [detectedDimensions, setDetectedDimensions] = useState<number | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tested, setTested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const providerOptions = useMemo(
    () => (settings?.providers ?? []).filter((row) => row.configured),
    [settings],
  );

  const failure = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : String(reason));

  useEffect(() => {
    let alive = true;
    fetchSettings(apiToken)
      .then((payload) => {
        if (!alive) return;
        setSettings(payload);
        const embedding = payload.memory?.embedding;
        if (embedding?.enabled) {
          setProvider(embedding.provider ?? "");
          setApiBase(embedding.api_base ?? "");
          setModel(embedding.model);
          setDimensions(embedding.dimensions ? String(embedding.dimensions) : "");
          setMultimodal(embedding.multimodal);
          setTested(true);
        }
      })
      .catch(failure);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  const test = async () => {
    if (!online || testing) return;
    if (!model.trim()) {
      setError(t("onboarding.embedding.modelRequired"));
      return;
    }
    if (!provider && !apiBase.trim()) {
      setError(t("onboarding.embedding.endpointRequired"));
      return;
    }
    setTesting(true);
    setError(null);
    try {
      const result = await testEmbeddingEndpoint(client, {
        provider: provider === CUSTOM_ENDPOINT ? "" : provider,
        apiBase: apiBase.trim(),
        apiKey: apiKey.trim(),
        model: model.trim(),
        dimensions: dimensions.trim() ? Number(dimensions) : null,
        multimodal,
      });
      setTested(true);
      setDetectedDimensions(result.dimensions || null);
    } catch (reason) {
      setTested(false);
      failure(reason);
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    if (!settings || saving || !online) return;
    if (!model.trim()) {
      setError(t("onboarding.embedding.modelRequired"));
      return;
    }
    if (!provider && !apiBase.trim()) {
      setError(t("onboarding.embedding.endpointRequired"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const base = memoryFormFromPayload(settings);
      await updateMemorySettings(client, {
        ...base,
        embeddingEnabled: true,
        embeddingProvider: provider === CUSTOM_ENDPOINT ? "" : provider,
        embeddingModel: model.trim(),
        embeddingApiBase: apiBase.trim(),
        ...(apiKey.trim() ? { embeddingApiKey: apiKey.trim() } : {}),
        embeddingDimensions: dimensions.trim() ? Number(dimensions) : null,
        embeddingMultimodal: multimodal,
      });
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

  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.24em] text-muted-foreground/70">
        {t("onboarding.embedding.eyebrow")}
      </p>
      <h1 className="display-serif mt-3 text-[clamp(1.9rem,4.8vw,2.7rem)] leading-[1.12] text-foreground/95">
        {t("onboarding.embedding.title")}
      </h1>
      <p className="mt-3 max-w-lg text-[13.5px] leading-6 text-muted-foreground">
        {t("onboarding.embedding.body")}
      </p>

      <div className="mt-7 space-y-4 rounded-[1.75rem] border border-white/55 bg-white/40 p-5 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.04] sm:p-6">
        <div>
          <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-embedding-provider">
            {t("onboarding.embedding.provider")}
          </label>
          <Select
            value={provider || CUSTOM_ENDPOINT}
            onValueChange={(value) => {
              const next = value === CUSTOM_ENDPOINT ? "" : value;
              setProvider(next);
              setTested(false);
              setApiKey("");
              const row = settings.providers.find((entry) => entry.name === next);
              setApiBase(row?.api_base ?? row?.default_api_base ?? "");
            }}
          >
            <SelectTrigger
              id="onboarding-embedding-provider"
              className="h-12 w-full justify-between rounded-full border-white/60 bg-white/60 text-[14px] font-normal dark:border-white/10 dark:bg-white/[0.06]"
            >
              <SelectValue placeholder={t("onboarding.embedding.providerPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={CUSTOM_ENDPOINT}>{t("onboarding.embedding.customEndpoint")}</SelectItem>
              {providerOptions.map((entry) => (
                <SelectItem key={entry.name} value={entry.name}>{entry.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="onboarding-field-enter grid gap-4 sm:grid-cols-[1.4fr_1fr]">
          <div>
            <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-embedding-model">
              {t("onboarding.embedding.model")}
            </label>
            <Input
              id="onboarding-embedding-model"
              value={model}
              onChange={(event) => { setModel(event.target.value); setTested(false); setDetectedDimensions(null); }}
              placeholder="text-embedding-3-small"
              autoComplete="off"
              className="h-12 rounded-full border-white/60 bg-white/60 px-5 font-mono text-[13px] dark:border-white/10 dark:bg-white/[0.06]"
            />
          </div>
          <div>
            <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-embedding-dimensions">
              {t("onboarding.embedding.dimensions")}
            </label>
            <Input
              id="onboarding-embedding-dimensions"
              value={dimensions}
              onChange={(event) => { setDimensions(event.target.value); setTested(false); }}
              placeholder={t("onboarding.embedding.dimensionsPlaceholder")}
              inputMode="numeric"
              autoComplete="off"
              className="h-12 rounded-full border-white/60 bg-white/60 px-5 text-[13px] dark:border-white/10 dark:bg-white/[0.06]"
            />
            {detectedDimensions ? (
              <p className="mt-2 text-[12px] text-muted-foreground">
                {t("onboarding.embedding.detectedDimensions", { count: detectedDimensions })}
              </p>
            ) : null}
          </div>
        </div>

        <label className="flex items-start gap-3 rounded-[1.25rem] border border-white/60 bg-white/45 px-4 py-3 dark:border-white/10 dark:bg-white/[0.05]">
          <input
            type="checkbox"
            checked={multimodal}
            onChange={(event) => { setMultimodal(event.target.checked); setTested(false); }}
            className="mt-0.5 size-4 accent-foreground"
          />
          <span>
            <span className="block text-[13px] text-foreground">{t("onboarding.embedding.multimodal")}</span>
            <span className="mt-0.5 block text-[12px] leading-5 text-muted-foreground">
              {t("onboarding.embedding.multimodalHelp")}
            </span>
          </span>
        </label>

        <div>
          <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-embedding-base">
            {t("onboarding.embedding.apiBase")}
          </label>
          <Input
            id="onboarding-embedding-base"
            value={apiBase}
            onChange={(event) => { setApiBase(event.target.value); setTested(false); }}
            placeholder={provider ? t("onboarding.embedding.providerBase") : "https://api.example.com/v1"}
            inputMode="url"
            autoComplete="url"
            className="h-12 rounded-full border-white/60 bg-white/60 px-5 font-mono text-[13px] dark:border-white/10 dark:bg-white/[0.06]"
          />
        </div>

        <div>
          <label className="mb-2 block text-[12.5px] text-muted-foreground" htmlFor="onboarding-embedding-key">
            {t("onboarding.embedding.apiKey")}
          </label>
          <Input
            id="onboarding-embedding-key"
            type="password"
            value={apiKey}
            onChange={(event) => { setApiKey(event.target.value); setTested(false); }}
            placeholder={provider ? t("onboarding.embedding.apiKeyProvider") : t("onboarding.embedding.apiKeyPlaceholder")}
            autoComplete="off"
            className="h-12 rounded-full border-white/60 bg-white/60 px-5 text-[14px] dark:border-white/10 dark:bg-white/[0.06]"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => void test()}
            disabled={!online || testing}
            className="h-10 rounded-full border-foreground/15 px-5 text-[13px]"
          >
            {testing
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              : <PlugZap className="h-3.5 w-3.5" aria-hidden />}
            {t("onboarding.embedding.test")}
          </Button>
          {tested ? (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-emerald-700 dark:text-emerald-400">
              <Check className="h-3.5 w-3.5" aria-hidden />
              {t("onboarding.embedding.connected")}
            </span>
          ) : error ? (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-destructive">
              <CircleAlert className="h-3.5 w-3.5" aria-hidden />
              {t("onboarding.embedding.connectFailed")}
            </span>
          ) : null}
        </div>

        {error ? (
          <p role="alert" className="rounded-xl bg-destructive/8 px-3 py-2 text-[12.5px] leading-5 text-destructive">
            {error}
          </p>
        ) : null}

        <p className="text-[12px] leading-5 text-muted-foreground/75">
          {t("onboarding.embedding.optionalHint")}
        </p>
      </div>

      <div className="mt-7 flex flex-wrap items-center justify-between gap-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t("onboarding.back")}
        </button>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onDone}
            disabled={busy || saving || !online}
            className="text-[13px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            {t("onboarding.embedding.skip")}
          </button>
          <Button
            onClick={() => void save()}
            disabled={!online || saving || busy || !model.trim()}
            className="h-11 rounded-full px-6"
          >
            {saving || busy
              ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              : <Sparkles className="h-4 w-4" aria-hidden />}
            <span>{finishLabel}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
