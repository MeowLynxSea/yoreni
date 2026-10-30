import { useEditCompletion, type EditCompletion } from "@/components/settings/shared/useEditCompletion";
import { SettingsAdvancedOptions } from "@/components/settings/shared/SettingsFeature";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import {
  NumberInput,
  RestartSettingsFooter,
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
  StatusPill,
} from "@/components/settings/shared/SettingsControls";
import { ToggleButton } from "@/components/ui/toggle-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { MemorySettingsUpdate, SettingsPayload } from "@/lib/types";

const CUSTOM_ENDPOINT = "__custom__";

export const DEFAULT_MEMORY_SETTINGS: NonNullable<SettingsPayload["memory"]> = {
  enabled: true,
  core_token_budget: 3000,
  retrieval_token_budget: 4000,
  retrieval_limit: 8,
  embedding: {
    enabled: false,
    provider: null,
    model: "",
    api_base: null,
    api_key_hint: null,
    dimensions: null,
    batch_size: 32,
  },
};

export const DEFAULT_MEMORY_FORM: MemorySettingsUpdate = {
  enabled: true,
  coreTokenBudget: 3000,
  retrievalTokenBudget: 4000,
  retrievalLimit: 8,
  embeddingEnabled: false,
  embeddingProvider: "",
  embeddingModel: "",
  embeddingApiBase: "",
  embeddingDimensions: null,
  embeddingBatchSize: 32,
};

export function memoryFormFromPayload(payload: SettingsPayload): MemorySettingsUpdate {
  const memory = payload.memory ?? DEFAULT_MEMORY_SETTINGS;
  return {
    enabled: memory.enabled,
    coreTokenBudget: memory.core_token_budget,
    retrievalTokenBudget: memory.retrieval_token_budget,
    retrievalLimit: memory.retrieval_limit,
    embeddingEnabled: memory.embedding.enabled,
    embeddingProvider: memory.embedding.provider ?? "",
    embeddingModel: memory.embedding.model,
    embeddingApiBase: memory.embedding.api_base ?? "",
    embeddingDimensions: memory.embedding.dimensions,
    embeddingBatchSize: memory.embedding.batch_size,
  };
}

export function MemorySettings({
  embedded = false,
  editCompletion,
  error,
  settings,
  form,
  dirty,
  saving,
  onChangeForm,
  onSave,
  onRestart,
  isRestarting,
  requiresRestartPending,
  children,
}: {
  embedded?: boolean;
  editCompletion?: EditCompletion;
  error?: string;
  settings: SettingsPayload;
  form: MemorySettingsUpdate;
  dirty: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<MemorySettingsUpdate>>;
  onSave: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
  requiresRestartPending: boolean;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const localEdits = useEditCompletion(form, dirty, onSave, !editCompletion);
  const edits = editCompletion ?? localEdits;
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const memory = settings.memory ?? DEFAULT_MEMORY_SETTINGS;
  const providers = settings.providers ?? [];
  const selectedProvider = providers.find(
    (provider) => provider.name === form.embeddingProvider,
  );
  const providerBase = selectedProvider?.default_api_base ?? selectedProvider?.api_base ?? "";
  const keyHint = form.embeddingApiKey === undefined
    ? memory.embedding.api_key_hint ?? (selectedProvider?.configured
      ? tx("settings.memory.providerKey", "Uses the provider key")
      : null)
    : null;

  return (
    <section onChangeCapture={edits.onChangeCapture} onFocusCapture={edits.onFocusCapture} onBlurCapture={edits.onBlurCapture}>
      {!embedded ? <SettingsSectionTitle>{tx("settings.sections.memory", "Long-term memory")}</SettingsSectionTitle> : null}
      <SettingsGroup flat={embedded}>
        {!embedded ? (
          <SettingsRow
            title={tx("settings.rows.memory", "Long-term memory")}
            description={tx("settings.help.memory", "Remember durable preferences, decisions and project knowledge across conversations.")}
          >
            <ToggleButton
              checked={form.enabled}
              onChange={(enabled) => { edits.complete(); onChangeForm((prev) => ({ ...prev, enabled })); }}
              ariaLabel={tx("settings.rows.memory", "Long-term memory")}
              label={form.enabled ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
        ) : null}
        {form.enabled ? <>
          <SettingsRow
            title={tx("settings.rows.memoryEmbedding", "Semantic search")}
            description={tx("settings.help.memoryEmbedding", "Use an OpenAI-compatible embedding model to find memories by meaning. Without it, search stays keyword-only.")}
          >
            <ToggleButton
              checked={form.embeddingEnabled}
              onChange={(embeddingEnabled) => {
                edits.complete();
                onChangeForm((prev) => ({ ...prev, embeddingEnabled }));
              }}
              ariaLabel={tx("settings.rows.memoryEmbedding", "Semantic search")}
              label={form.embeddingEnabled
                ? tx("settings.values.on", "On")
                : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          {form.embeddingEnabled ? <>
            <SettingsRow
              title={tx("settings.rows.memoryEmbeddingProvider", "Provider")}
              description={tx("settings.help.memoryEmbeddingProvider", "Reuse a configured provider's API base and key, or enter a custom endpoint below.")}
            >
              <Select
                value={form.embeddingProvider || CUSTOM_ENDPOINT}
                onValueChange={(provider) => {
                  edits.complete();
                  onChangeForm((prev) => ({
                    ...prev,
                    embeddingProvider: provider === CUSTOM_ENDPOINT ? "" : provider,
                  }));
                }}
              >
                <SelectTrigger
                  aria-label={tx("settings.rows.memoryEmbeddingProvider", "Provider")}
                  className="w-full justify-between rounded-full border-white/55 bg-white/45 text-[13px] font-normal shadow-none backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
                >
                  <SelectValue>
                    <span className="truncate">
                      {selectedProvider?.label ?? tx("settings.memory.customEndpoint", "Custom endpoint")}
                    </span>
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={CUSTOM_ENDPOINT}>
                    {tx("settings.memory.customEndpoint", "Custom endpoint")}
                  </SelectItem>
                  {providers.map((provider) => (
                    <SelectItem key={provider.name} value={provider.name}>
                      {provider.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingsRow>
            <SettingsRow
              title={tx("settings.rows.memoryEmbeddingBase", "API base")}
              description={tx("settings.help.memoryEmbeddingBase", "The OpenAI-compatible /v1 endpoint. Leave empty to reuse the selected provider.")}
            >
              <Input
                value={form.embeddingApiBase}
                onChange={(event) => onChangeForm((prev) => ({ ...prev, embeddingApiBase: event.target.value }))}
                aria-label={tx("settings.rows.memoryEmbeddingBase", "API base")}
                placeholder={providerBase || "https://api.openai.com/v1"}
                className="h-9 w-full rounded-full text-end text-[13px]"
              />
            </SettingsRow>
            <SettingsRow
              title={tx("settings.rows.memoryEmbeddingKey", "API key")}
              description={tx("settings.help.memoryEmbeddingKey", "Stored in the local config and never shown again. Leave empty to reuse the provider key.")}
            >
              <div className="flex w-full items-center gap-2">
                <Input
                  type="password"
                  value={form.embeddingApiKey ?? ""}
                  onChange={(event) => onChangeForm((prev) => ({ ...prev, embeddingApiKey: event.target.value }))}
                  aria-label={tx("settings.rows.memoryEmbeddingKey", "API key")}
                  placeholder={keyHint ?? ""}
                  className="h-9 min-w-0 flex-1 rounded-full text-end text-[13px]"
                />
                {form.embeddingApiKey !== undefined ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="rounded-full"
                    onClick={() => onChangeForm((prev) => ({ ...prev, embeddingApiKey: "" }))}
                  >
                    {tx("settings.memory.clearKey", "Clear")}
                  </Button>
                ) : null}
              </div>
            </SettingsRow>
            <SettingsRow
              title={tx("settings.rows.memoryEmbeddingModel", "Model")}
              description={tx("settings.help.memoryEmbeddingModel", "The embedding model id, for example text-embedding-3-small or nomic-embed-text.")}
            >
              <Input
                value={form.embeddingModel}
                onChange={(event) => onChangeForm((prev) => ({ ...prev, embeddingModel: event.target.value }))}
                aria-label={tx("settings.rows.memoryEmbeddingModel", "Model")}
                placeholder="text-embedding-3-small"
                className="h-9 w-full rounded-full text-end text-[13px]"
              />
            </SettingsRow>
            <SettingsRow
              title={tx("settings.rows.memoryEmbeddingDimensions", "Dimensions")}
              description={tx("settings.help.memoryEmbeddingDimensions", "Optional. Set it only when the endpoint requires an explicit vector size; changing it starts a new index.")}
            >
              <div className="flex w-full items-center justify-end gap-2">
                <StatusPill tone={memory.embedding.enabled ? "success" : "neutral"}>
                  {memory.embedding.enabled
                    ? tx("settings.values.configured", "Configured")
                    : tx("settings.values.notConfigured", "Not configured")}
                </StatusPill>
                <div className="w-28">
                  <NumberInput
                    value={form.embeddingDimensions ?? Number.NaN}
                    min={1}
                    max={8192}
                    ariaLabel={tx("settings.rows.memoryEmbeddingDimensions", "Dimensions")}
                    onChange={(value) => onChangeForm((prev) => ({
                      ...prev,
                      embeddingDimensions: Number.isFinite(value) ? value : null,
                    }))}
                  />
                </div>
              </div>
            </SettingsRow>
            <SettingsAdvancedOptions>
              <SettingsRow title={tx("settings.rows.memoryEmbeddingBatch", "Embedding batch size")}>
                <NumberInput
                  value={form.embeddingBatchSize}
                  min={1}
                  max={64}
                  ariaLabel={tx("settings.rows.memoryEmbeddingBatch", "Embedding batch size")}
                  onChange={(embeddingBatchSize) => onChangeForm((prev) => ({ ...prev, embeddingBatchSize }))}
                />
              </SettingsRow>
            </SettingsAdvancedOptions>
          </> : null}
          <SettingsAdvancedOptions>
            <SettingsRow
              title={tx("settings.rows.memoryCoreBudget", "Core memory budget")}
              description={tx("settings.help.memoryCoreBudget", "Tokens reserved for SOUL.md, USER.md and memory/MEMORY.md in every conversation.")}
            >
              <NumberInput
                value={form.coreTokenBudget}
                min={400}
                max={32000}
                suffix="t"
                ariaLabel={tx("settings.rows.memoryCoreBudget", "Core memory budget")}
                onChange={(coreTokenBudget) => onChangeForm((prev) => ({ ...prev, coreTokenBudget }))}
              />
            </SettingsRow>
            <SettingsRow
              title={tx("settings.rows.memoryRetrievalBudget", "Retrieved memory budget")}
              description={tx("settings.help.memoryRetrievalBudget", "Tokens available for facts retrieved for the current turn.")}
            >
              <NumberInput
                value={form.retrievalTokenBudget}
                min={200}
                max={32000}
                suffix="t"
                ariaLabel={tx("settings.rows.memoryRetrievalBudget", "Retrieved memory budget")}
                onChange={(retrievalTokenBudget) => onChangeForm((prev) => ({ ...prev, retrievalTokenBudget }))}
              />
            </SettingsRow>
            <SettingsRow title={tx("settings.rows.memoryRetrievalLimit", "Retrieved facts")}>
              <NumberInput
                value={form.retrievalLimit}
                min={1}
                max={50}
                ariaLabel={tx("settings.rows.memoryRetrievalLimit", "Retrieved facts")}
                onChange={(retrievalLimit) => onChangeForm((prev) => ({ ...prev, retrievalLimit }))}
              />
            </SettingsRow>
          </SettingsAdvancedOptions>
        </> : null}
        {form.enabled ? children : null}
        <RestartSettingsFooter
          error={Boolean(error)}
          message={error}
          autoSave
          dirty={dirty}
          saving={saving}
          pendingRestart={requiresRestartPending}
          dirtyMessage={tx("settings.status.restartAfterSaving", "Save changes, then restart yoreni to apply them.")}
          pendingMessage={tx("settings.status.savedRestartApply", "Saved. Restart to apply changes.")}
          onSave={onSave}
          onRestart={onRestart}
          isRestarting={isRestarting}
        />
      </SettingsGroup>
    </section>
  );
}
