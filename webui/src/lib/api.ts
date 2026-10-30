
import type { ApiServicePayload, AutomationsPayload, AutomaticModelAPIPayload, AutomationUpdatePayload, ChannelConfigurePayload, ChannelConnectPayload, ChannelValidationPayload, CliAppsPayload, DynamicWidgetSnapshot, DynamicWidgetSummary, FilePreviewPayload, FileReferenceMetadata, GoalStateWsPayload, HomeWidgetsPayload, ImageGenerationSettingsUpdate, McpPresetsPayload, McpOAuthFlowPayload, MarketplaceProvider, MemorySettingsUpdate, YoreniFeaturesPayload, ModelConfigurationCreate, ModelConfigurationUpdate, NetworkSafetySettingsUpdate, PairingPayload, ProviderCreationUpdate, ProviderModelsPayload, ProviderOAuthCompletionResult, ProviderOAuthLoginResult, ProviderSettingsUpdate, SettingsPayload, SkillDetail, SkillActionPayload, SkillInstallPayload, SkillsPayload, SkillsSearchPayload, SkillsTrendsPayload, SkillsTrendingPayload, TranscriptionSettingsUpdate, UIMediaWireAttachment, WebSearchSettingsUpdate, WorkspacesPayload, WorkspaceDirectoriesPayload } from "./types";
import { fetchWithTimeout } from "./http";

const API_READ_TIMEOUT_MS = 20_000;
const API_MUTATION_TIMEOUT_MS = 20_000;
const PACKAGE_MUTATION_TIMEOUT_MS = 150_000;
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export interface WebUIMutationTransport {
  requestMutation<T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T>;
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
  timeoutMs: number = 0,
): Promise<T> {
  const res = await fetchWithTimeout(
    url,
    {
      ...(init ?? {}),
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${token}`,
      },
      credentials: "same-origin",
    },
    timeoutMs,
  );
  if (!res.ok) {
    const text = typeof res.text === "function" ? (await res.text()).trim() : "";
    let message = text;
    if (text.startsWith("{")) {
      try {
        const payload: unknown = JSON.parse(text);
        if (payload && typeof payload === "object") {
          const error = (payload as { error?: unknown }).error;
          if (typeof error === "string" && error.trim()) message = error.trim();
        }
      } catch {
        // Preserve non-JSON error bodies exactly as returned by the gateway.
      }
    }
    throw new ApiError(res.status, message || `HTTP ${res.status}`);
  }
  const contentType = res.headers?.get?.("content-type") ?? "";
  if (contentType && !contentType.toLowerCase().includes("application/json")) {
    const text = typeof res.text === "function" ? await res.text() : "";
    const isHtml = text.trimStart().toLowerCase().startsWith("<!doctype");
    throw new ApiError(
      res.status,
      isHtml
        ? "Gateway returned WebUI HTML instead of JSON. Restart yoreni gateway and try again."
        : "Gateway returned a non-JSON response.",
    );
  }
  return (await res.json()) as T;
}

async function mutation<T>(
  transport: WebUIMutationTransport,
  action: string,
  payload: Record<string, unknown> = {},
  timeoutMs: number = API_MUTATION_TIMEOUT_MS,
): Promise<T> {
  try {
    return await transport.requestMutation<T>(action, payload, timeoutMs);
  } catch (reason) {
    const status = (
      typeof reason === "object"
      && reason !== null
      && "status" in reason
      && typeof reason.status === "number"
    ) ? reason.status : 500;
    const message = reason instanceof Error ? reason.message : "WebUI mutation failed";
    throw new ApiError(status, message);
  }
}

function compactMcpValues(values: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) payload[key] = trimmed;
      return;
    }
    payload[key] = value;
  });
  return payload;
}

export async function fetchFilePreview(
  token: string,
  path: string,
  base: string = "",
): Promise<FilePreviewPayload> {
  const query = new URLSearchParams();
  query.set("path", path);
  return request<FilePreviewPayload>(
    `${base}/api/agent/file-preview?${query}`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFileReferenceMetadata(
  token: string,
  path: string,
): Promise<FileReferenceMetadata> {
  const query = new URLSearchParams({ path, metadata: "1" });
  return request<FileReferenceMetadata>(
    `/api/agent/file-preview?${query}`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchFilePreviewAvailability(
  token: string,
  path: string,
  base: string = "",
): Promise<boolean> {
  const query = new URLSearchParams();
  query.set("path", path);
  query.set("probe", "1");
  const payload = await request<{ available?: boolean }>(
    `${base}/api/agent/file-preview?${query}`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
  return payload.available !== false;
}

export async function fetchAutomations(
  token: string,
  base: string = "",
): Promise<AutomationsPayload> {
  return request<AutomationsPayload>(
    `${base}/api/webui/automations`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchGoal(
  token: string,
  base: string = "",
): Promise<GoalStateWsPayload> {
  const payload = await request<{ goal?: GoalStateWsPayload }>(
    `${base}/api/webui/goal`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
  return payload.goal ?? { active: false };
}

export async function fetchAutomationRunResult(
  token: string,
  id: string,
  runAtMs: number,
  kind: "cron" | "local_trigger",
  signal?: AbortSignal,
): Promise<{ response: string | null }> {
  const query = new URLSearchParams({ id, run_at_ms: String(runAtMs), kind });
  return request<{ response: string | null }>(
    `/api/webui/automations/result?${query}`, token, { signal }, API_READ_TIMEOUT_MS,
  );
}

export async function runAutomationAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "delete" | "run",
  id: string,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, `automation.${action}`, { id });
}

export async function updateAutomation(
  transport: WebUIMutationTransport,
  id: string,
  values: AutomationUpdatePayload,
): Promise<AutomationsPayload> {
  return mutation<AutomationsPayload>(transport, "automation.update", { id, values });
}

export async function fetchSkills(
  token: string,
  base: string = "",
): Promise<SkillsPayload> {
  return request<SkillsPayload>(
    `${base}/api/webui/skills`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSkillDetail(
  token: string,
  name: string,
  base: string = "",
): Promise<SkillDetail> {
  return request<SkillDetail>(
    `${base}/api/webui/skills/${encodeURIComponent(name)}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function updateSkillEnabled(
  transport: WebUIMutationTransport,
  name: string,
  enabled: boolean,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.update", { name, enabled });
}

export async function deleteSkill(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SkillActionPayload> {
  return mutation<SkillActionPayload>(transport, "skill.delete", { name });
}

export async function searchMarketplaceSkills(
  token: string,
  query: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsSearchPayload> {
  const params = new URLSearchParams({ q: query, provider });
  return request<SkillsSearchPayload>(
    `${base}/api/webui/skills/search?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchTrendingMarketplaceSkills(
  token: string,
  provider: MarketplaceProvider = "all",
  base: string = "",
): Promise<SkillsTrendingPayload> {
  const params = new URLSearchParams({ provider });
  return request<SkillsTrendingPayload>(
    `${base}/api/webui/skills/trending?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchMarketplaceSkillTrends(
  token: string,
  skillIds: string[],
  base: string = "",
): Promise<SkillsTrendsPayload> {
  const params = new URLSearchParams();
  skillIds.forEach((id) => params.append("id", id));
  return request<SkillsTrendsPayload>(
    `${base}/api/webui/skills/trends?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function installMarketplaceSkill(
  transport: WebUIMutationTransport,
  provider: Exclude<MarketplaceProvider, "all">,
  source: string,
  skill: string,
  version: string = "",
): Promise<SkillInstallPayload> {
  return mutation<SkillInstallPayload>(
    transport,
    "skill.install",
    { provider, source, skill, ...(version ? { version } : {}) },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function fetchSettings(
  token: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    `${base}/api/settings`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchSettingsUsage(
  token: string,
  base: string = "",
): Promise<NonNullable<SettingsPayload["usage"]>> {
  return request<NonNullable<SettingsPayload["usage"]>>(
    `${base}/api/settings/usage`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchHomeWidgets(
  token: string,
  base: string = "",
): Promise<HomeWidgetsPayload> {
  return request<HomeWidgetsPayload>(
    `${base}/api/webui/home-widgets`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
}

export async function saveHomeWidgets(
  transport: WebUIMutationTransport,
  layout: HomeWidgetsPayload,
): Promise<HomeWidgetsPayload> {
  return mutation<HomeWidgetsPayload>(transport, "webui.home_widgets.update", {
    order: layout.order,
    widgets: layout.widgets,
  });
}

export async function fetchWidgets(
  token: string,
  base: string = "",
): Promise<{ widgets: DynamicWidgetSummary[] }> {
  return request<{ widgets: DynamicWidgetSummary[] }>(
    `${base}/api/webui/widgets`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWidget(
  token: string,
  widgetId: string,
  base: string = "",
): Promise<DynamicWidgetSnapshot> {
  return request<DynamicWidgetSnapshot>(
    `${base}/api/webui/widgets/${encodeURIComponent(widgetId)}`,
    token,
    { cache: "no-store" },
    API_READ_TIMEOUT_MS,
  );
}

export async function deleteWidget(
  transport: WebUIMutationTransport,
  widgetId: string,
): Promise<{ deleted: string }> {
  return mutation<{ deleted: string }>(transport, "webui.widgets.delete", {
    widget_id: widgetId,
  });
}

export async function refreshWidget(
  transport: WebUIMutationTransport,
  widgetId: string,
): Promise<{ widget_id: string; data: unknown; duration_ms: number }> {
  return mutation<{ widget_id: string; data: unknown; duration_ms: number }>(
    transport,
    "webui.widgets.refresh",
    { widget_id: widgetId },
  );
}

export async function fetchWorkspaces(
  token: string,
  base: string = "",
): Promise<WorkspacesPayload> {
  return request<WorkspacesPayload>(
    `${base}/api/workspaces`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchWorkspaceDirectories(
  token: string,
  path: string,
  query: string,
  showHidden: boolean,
  allowPartial = false,
): Promise<WorkspaceDirectoriesPayload> {
  const params = new URLSearchParams({ path, q: query, hidden: showHidden ? "1" : "0", partial: allowPartial ? "1" : "0" });
  return request<WorkspaceDirectoriesPayload>(
    `/api/workspaces/directories?${params}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchInstalledCliApps(
  token: string,
  base: string = "",
): Promise<CliAppsPayload> {
  return request<CliAppsPayload>(
    `${base}/api/settings/cli-apps?installed_only=1`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchYoreniFeatures(
  token: string,
  base: string = "",
): Promise<YoreniFeaturesPayload> {
  return request<YoreniFeaturesPayload>(
    `${base}/api/settings/yoreni-features`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function fetchApiService(token: string, base: string = ""): Promise<ApiServicePayload> {
  return request<ApiServicePayload>(`${base}/api/settings/api-service`, token);
}

export async function startApiService(
  transport: WebUIMutationTransport,
  values: { host: string; port: number; timeout: number; apiKey?: string },
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(
    transport,
    "settings.api_service.start",
    {
      host: values.host,
      port: values.port,
      timeout: values.timeout,
      ...(values.apiKey !== undefined ? { api_key: values.apiKey } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function stopApiService(
  transport: WebUIMutationTransport,
): Promise<ApiServicePayload> {
  return mutation<ApiServicePayload>(transport, "settings.api_service.stop");
}

export async function enableYoreniFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: { instanceId?: string; installOnly?: boolean } = {},
): Promise<YoreniFeaturesPayload> {
  return mutation<YoreniFeaturesPayload>(
    transport,
    "settings.feature.enable",
    {
      name,
      ...(options.instanceId ? { instance_id: options.instanceId } : {}),
      ...(options.installOnly ? { install_only: true } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function disableYoreniFeature(
  transport: WebUIMutationTransport,
  name: string,
  options: { instanceId?: string } = {},
): Promise<YoreniFeaturesPayload> {
  return mutation<YoreniFeaturesPayload>(
    transport,
    "settings.feature.disable",
    { name, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
  );
}

export async function fetchPairingRequests(
  token: string,
  base: string = "",
): Promise<PairingPayload> {
  return request<PairingPayload>(
    `${base}/api/settings/pairing`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runPairingAction(
  transport: WebUIMutationTransport,
  action: "approve" | "deny",
  code: string,
): Promise<PairingPayload> {
  return mutation<PairingPayload>(transport, `settings.pairing.${action}`, { code });
}

export async function startChannelConnect<T = ChannelConnectPayload>(
  transport: WebUIMutationTransport,
  channel: string,
  params: Readonly<Record<string, string | boolean>> = {},
): Promise<T> {
  return mutation<T>(
    transport,
    "settings.channel.connect.start",
    {
      ...params,
      channel,
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function pollChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
  params: Readonly<Record<string, string>> = {},
): Promise<ChannelConnectPayload> {
  const values = Object.fromEntries(
    Object.entries(params).filter(([key]) => key !== "session_id"),
  );
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.poll",
    { channel, session_id: sessionId, ...values },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function cancelChannelConnect(
  transport: WebUIMutationTransport,
  channel: string,
  sessionId: string,
): Promise<ChannelConnectPayload> {
  return mutation<ChannelConnectPayload>(
    transport,
    "settings.channel.connect.cancel",
    { channel, session_id: sessionId },
  );
}

export async function configureChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string | null>,
  options: { enable?: boolean; instanceId?: string } = {},
): Promise<ChannelConfigurePayload> {
  return mutation<ChannelConfigurePayload>(
    transport,
    "settings.channel.configure",
    {
      name,
      values,
      ...(options.enable !== undefined ? { enable: options.enable } : {}),
      ...(options.instanceId ? { instance_id: options.instanceId } : {}),
    },
    PACKAGE_MUTATION_TIMEOUT_MS,
  );
}

export async function validateChannel(
  transport: WebUIMutationTransport,
  name: string,
  values: Record<string, string | null> = {},
  options: { instanceId?: string } = {},
): Promise<ChannelValidationPayload> {
  return mutation<ChannelValidationPayload>(
    transport,
    "settings.channel.validate",
    { name, values, ...(options.instanceId ? { instance_id: options.instanceId } : {}) },
  );
}

export async function runCliAppAction(
  transport: WebUIMutationTransport,
  action: "install" | "update" | "uninstall" | "test",
  name: string,
): Promise<CliAppsPayload> {
  return mutation<CliAppsPayload>(
    transport,
    `settings.cli_app.${action}`,
    { name },
    action === "install" || action === "update"
      ? PACKAGE_MUTATION_TIMEOUT_MS
      : API_MUTATION_TIMEOUT_MS,
  );
}

export async function fetchMcpPresets(
  token: string,
  base: string = "",
): Promise<McpPresetsPayload> {
  return request<McpPresetsPayload>(
    `${base}/api/settings/mcp-presets`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function startMcpOAuth(
  transport: WebUIMutationTransport,
  name: string,
  reset: boolean = false,
  values: Record<string, string> = {},
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_start",
    { ...values, name, ...(reset ? { reset: true } : {}) },
    30_000,
  );
}

export async function fetchMcpOAuthStatus(
  token: string,
  flowId: string,
  base: string = "",
): Promise<McpOAuthFlowPayload> {
  const query = new URLSearchParams({ flow_id: flowId });
  return request<McpOAuthFlowPayload>(
    `${base}/api/settings/mcp-oauth/status?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function completeMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
  callbackUrl: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_complete",
    { flow_id: flowId, callback_url: callbackUrl },
  );
}

export async function cancelMcpOAuth(
  transport: WebUIMutationTransport,
  flowId: string,
): Promise<McpOAuthFlowPayload> {
  return mutation<McpOAuthFlowPayload>(
    transport,
    "settings.mcp.oauth_cancel",
    { flow_id: flowId },
  );
}

export async function fetchAutomaticModelAPI(
  token: string,
  provider: string,
  model: string,
  reasoningEffort: string,
  base: string = "",
): Promise<AutomaticModelAPIPayload> {
  const query = new URLSearchParams({ provider, model, reasoning_effort: reasoningEffort });
  const payload = await request<unknown>(
    `${base}/api/settings/model-api?${query}`, token, undefined, API_READ_TIMEOUT_MS,
  );
  if (typeof payload !== "object" || payload === null) throw new Error("Invalid model API resolution");
  const result = payload as Record<string, unknown>;
  const api = result.api;
  if (typeof result.provider !== "string" || (
    api !== "chat_completions" && api !== "responses" && api !== "anthropic_messages"
    && api !== "bedrock_converse" && api !== "transcription"
  )) throw new Error("Invalid model API resolution");
  return { provider: result.provider, api };
}

export async function fetchProviderModels(
  token: string,
  provider: string,
  base: string = "",
  options?: { refresh?: boolean },
): Promise<ProviderModelsPayload> {
  const query = new URLSearchParams();
  query.set("provider", provider);
  if (options?.refresh) query.set("refresh", "1");
  return request<ProviderModelsPayload>(
    `${base}/api/settings/provider-models?${query}`,
    token,
    undefined,
    API_READ_TIMEOUT_MS,
  );
}

export async function runMcpPresetAction(
  transport: WebUIMutationTransport,
  action: "enable" | "disable" | "remove" | "test" | "reconnect" | "proxy",
  name: string,
  values: Record<string, string> = {},
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    `settings.mcp.${action}`,
    { name, ...compactMcpValues(values) },
  );
}

export async function saveCustomMcpServer(
  transport: WebUIMutationTransport,
  values: Record<string, string>,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.custom",
    compactMcpValues(values),
  );
}

export async function importMcpConfig(
  transport: WebUIMutationTransport,
  config: string,
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(transport, "settings.mcp.import", { config });
}

export async function updateMcpServerTools(
  transport: WebUIMutationTransport,
  name: string,
  enabledTools: string[],
): Promise<McpPresetsPayload> {
  return mutation<McpPresetsPayload>(
    transport,
    "settings.mcp.tools",
    { name, enabled_tools: enabledTools },
  );
}

function modelGenerationSettingsPayload(
  configuration: Pick<
    ModelConfigurationCreate,
    "maxTokens" | "contextWindowTokens" | "temperature" | "reasoningEffort" | "api" | "extraHeaders" | "extraQuery" | "extraBody"
  >,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (configuration.maxTokens !== undefined) {
    payload.max_tokens = configuration.maxTokens;
  }
  if (configuration.contextWindowTokens !== undefined) {
    payload.context_window_tokens = configuration.contextWindowTokens;
  }
  if (configuration.temperature !== undefined) {
    payload.temperature = configuration.temperature;
  }
  if (configuration.reasoningEffort !== undefined) {
    payload.reasoning_effort = configuration.reasoningEffort ?? "";
  }
  if (configuration.api !== undefined) {
    payload.api = configuration.api;
  }
  for (const [key, field] of [
    ["extraHeaders", "extra_headers"], ["extraQuery", "extra_query"], ["extraBody", "extra_body"],
  ] as const) {
    if (configuration[key] !== undefined) payload[field] = configuration[key];
  }
  return payload;
}

export async function createModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationCreate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.create",
    {
      name: configuration.name,
      provider: configuration.provider,
      model: configuration.model,
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function updateModelConfiguration(
  transport: WebUIMutationTransport,
  configuration: ModelConfigurationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.update",
    {
      name: configuration.name,
      ...(configuration.newName !== undefined ? { new_name: configuration.newName } : {}),
      ...(configuration.provider !== undefined ? { provider: configuration.provider } : {}),
      ...(configuration.model !== undefined ? { model: configuration.model } : {}),
      ...modelGenerationSettingsPayload(configuration),
    },
  );
}

export async function deleteModelConfiguration(
  transport: WebUIMutationTransport,
  name: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.model_configuration.delete",
    { name },
  );
}

export async function migrateModelConfigurations(
  transport: WebUIMutationTransport,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_configuration.migrate");
}

export async function updateModelCallOrder(
  transport: WebUIMutationTransport,
  order: string[],
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.model_call_order.update", { order });
}

export async function updateProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.update", { ...update });
}

export async function createProviderSettings(
  transport: WebUIMutationTransport,
  update: ProviderCreationUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.create", { ...update });
}

export async function loginProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  remoteBrowserAccess: boolean = false,
): Promise<ProviderOAuthLoginResult> {
  return mutation<ProviderOAuthLoginResult>(
    transport,
    "settings.provider.oauth_login",
    { provider, ...(remoteBrowserAccess ? { remote_browser: true } : {}) },
  );
}

export async function completeProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  flowId: string,
  authorizationResponse?: string,
): Promise<ProviderOAuthCompletionResult> {
  return mutation<ProviderOAuthCompletionResult>(
    transport,
    "settings.provider.oauth_complete",
    {
      provider,
      flow_id: flowId,
      ...(authorizationResponse ? { authorization_response: authorizationResponse } : {}),
    },
  );
}

export async function cancelProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
  flowId: string,
): Promise<void> {
  await mutation(transport, "settings.provider.oauth_complete", {
    provider, flow_id: flowId, cancel: true,
  });
}

export async function logoutProviderOAuth(
  transport: WebUIMutationTransport,
  provider: string,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.provider.oauth_logout", { provider });
}

export async function updateWebSearchSettings(
  transport: WebUIMutationTransport,
  update: WebSearchSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.web_search.update",
    {
      provider: update.provider,
      ...(update.apiKey !== undefined ? { api_key: update.apiKey } : {}),
      ...(update.baseUrl !== undefined ? { base_url: update.baseUrl } : {}),
      ...(update.maxResults !== undefined ? { max_results: update.maxResults } : {}),
      ...(update.timeout !== undefined ? { timeout: update.timeout } : {}),
      ...(update.useJinaReader !== undefined
        ? { use_jina_reader: update.useJinaReader }
        : {}),
    },
  );
}

export async function updateNetworkSafetySettings(
  transport: WebUIMutationTransport,
  update: NetworkSafetySettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.network_safety.update",
    {
      webui_allow_local_service_access: update.webuiAllowLocalServiceAccess,
      webui_default_access_mode: update.webuiDefaultAccessMode,
    },
  );
}

export async function updateImageGenerationSettings(
  transport: WebUIMutationTransport,
  update: ImageGenerationSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.image_generation.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      default_aspect_ratio: update.defaultAspectRatio,
      default_image_size: update.defaultImageSize,
      max_images_per_turn: update.maxImagesPerTurn,
      ...(update.extraHeaders !== undefined ? { extra_headers: update.extraHeaders } : {}),
      ...(update.extraBody !== undefined ? { extra_body: update.extraBody } : {}),
    },
  );
}

export async function updateTranscriptionSettings(
  transport: WebUIMutationTransport,
  update: TranscriptionSettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.transcription.update",
    {
      enabled: update.enabled,
      provider: update.provider,
      model: update.model,
      language: update.language,
      max_duration_sec: update.maxDurationSec,
      max_upload_mb: update.maxUploadMb,
    },
  );
}


export async function updateMemorySettings(
  transport: WebUIMutationTransport,
  update: MemorySettingsUpdate,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(
    transport,
    "settings.memory.update",
    {
      enabled: update.enabled,
      core_token_budget: update.coreTokenBudget,
      retrieval_token_budget: update.retrievalTokenBudget,
      retrieval_limit: update.retrievalLimit,
      embedding_enabled: update.embeddingEnabled,
      embedding_provider: update.embeddingProvider,
      embedding_model: update.embeddingModel,
      embedding_api_base: update.embeddingApiBase,
      embedding_dimensions: update.embeddingDimensions ?? "",
      embedding_batch_size: update.embeddingBatchSize,
      ...(update.embeddingApiKey !== undefined ? { embedding_api_key: update.embeddingApiKey } : {}),
    },
  );
}


export async function updateRuntimeConfigSettings(
  transport: WebUIMutationTransport,
  values: Record<string, import("@/lib/types").RuntimeConfigValue>,
): Promise<SettingsPayload> {
  return mutation<SettingsPayload>(transport, "settings.runtime_config.update", { values });
}

export function starPromptAction(
  transport: WebUIMutationTransport,
  action: "claim" | "dismiss",
): Promise<{ show: boolean }> {
  return mutation<{ show: boolean }>(transport, `star_prompt.${action}`);
}

export interface PersonalAgentInfo {
  id: string;
  revision: number;
  pending_runs: { id: string; status: string; error: string | null }[];
  history?: AgentHistoryRun[];
}

/** One durable conversation row replayed in the same shapes the socket streams. */
export interface AgentHistoryEntry {
  event: "message" | "reaction" | "user_message";
  id?: string;
  text?: string;
  media_urls?: UIMediaWireAttachment[];
  reply_to?: string;
  quote?: string;
  rich?: boolean;
  target?: string;
  emoji?: string;
  message_id?: string;
  created_at_ms?: number;
}

export interface AgentHistoryRun {
  id: string;
  status: string;
  error: string | null;
  event_seq: number;
  event_id: string;
  source: string;
  kind: string;
  content: string;
  result: { content: string; stop_reason: string } | null;
  /** In-flight tool calls described for the working indicator. */
  activity?: { name: string; reason: string }[];
  /** Wall-clock admission time; ordering key for rows from different Runs. */
  created_at_ms?: number;
  /** Conversation projection version; 0 means the Run predates durable rows. */
  projection?: number;
  /** Durable rows in causal order, including inserted messages and reactions. */
  entries?: AgentHistoryEntry[];
}

export function fetchPersonalAgent(token: string, beforeSeq?: number): Promise<PersonalAgentInfo> {
  const query = beforeSeq === undefined ? "" : `?before_seq=${beforeSeq}`;
  return request<PersonalAgentInfo>(`/api/agent${query}`, token, undefined, API_READ_TIMEOUT_MS);
}

/** Search the whole durable transcript; the gateway owns the ranking. */
export function fetchChatSearch(token: string, query: string): Promise<unknown> {
  const params = new URLSearchParams({ q: query });
  return request<unknown>(`/api/webui/chat-search?${params}`, token, undefined, API_READ_TIMEOUT_MS);
}

/** The model-ranked tier may outlive an ordinary read, so it gets its own deadline. */
export function fetchChatSearchSemantic(token: string, query: string): Promise<unknown> {
  const params = new URLSearchParams({ q: query });
  return request<unknown>(
    `/api/webui/chat-search/semantic?${params}`,
    token,
    undefined,
    25_000,
  );
}
