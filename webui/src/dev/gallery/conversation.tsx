import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { FallbackResponseSources } from "@/components/ResponseSourceBadge";
import { AgentActivityCluster } from "@/components/thread/AgentActivityCluster";
import { ComposerUsagePopover, type ComposerRoundUsage } from "@/components/thread/ComposerUsagePopover";
import { ContextCompactionNotice } from "@/components/thread/ContextCompactionNotice";
import { ModelFallbackNotice } from "@/components/thread/ModelFallbackNotice";
import { ModelPresetBadge, type ModelPresetOption } from "@/components/thread/ModelPresetBadge";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { ThreadMessages } from "@/components/thread/ThreadMessages";
import { WorkspaceAccessMenu, WorkspaceProjectPicker } from "@/components/thread/WorkspaceControls";
import { WebSearchRun } from "@/components/thread/activity/WebSearchRun";
import { Button } from "@/components/ui/button";
import { formatToolCallTrace } from "@/lib/tool-traces";
import type { ToolProgressEvent, UIContextCompaction, UIMessage, WorkspaceScopePayload, WorkspacesPayload } from "@/lib/types";
import { Sample, StatePicker, type ComponentExample } from "./example";
import { useGalleryTranslation } from "./i18n";

const createdAt = Date.UTC(2026, 0, 1, 12);
const presets: ModelPresetOption[] = [
  { name: "日常", provider: "openai", model: "gpt-4.1", contextWindowTokens: 1_047_576 },
  { name: "推理", provider: "anthropic", model: "claude-sonnet-4", reasoningEffort: "high", contextWindowTokens: 200_000 },
];
const rounds: ComposerRoundUsage[] = [
  { id: "gallery-round-1", timestamp: createdAt, inputTokens: 8_200, cachedTokens: 6_000, outputTokens: 620, generationMs: 3_100 },
  { id: "gallery-round-2", timestamp: createdAt + 60_000, inputTokens: 12_000, cachedTokens: 8_200, outputTokens: 980, generationMs: 5_400 },
  { id: "gallery-round-3", timestamp: createdAt + 120_000, inputTokens: 18_500, cachedTokens: 12_000, outputTokens: 1_240, generationMs: 7_200 },
];

const projectScope: WorkspaceScopePayload = { project_path: "D:\\Projects\\yoreni", project_name: "yoreni", access_mode: "restricted" };
const workspaceCatalog: WorkspacesPayload = {
  schema_version: 1, default_access_mode: "default", default_scope: projectScope,
  recent_projects: [{ name: "yoreni", path: projectScope.project_path }, { name: "design-system", path: "D:\\Projects\\design-system" }],
  favorite_projects: [{ name: "yoreni", path: projectScope.project_path }],
  controls: { can_change_project: true },
};
const loadExampleProjects = () => Promise.resolve(workspaceCatalog);

const prompts: UIMessage[] = [
  { id: "gallery-prompt-1", role: "user", content: "整理组件的输入、状态和回调。", createdAt },
  { id: "gallery-answer-1", role: "assistant", content: "先从 **模型选择** 和 **消息输入** 开始。\n\n- 调用方提供数据\n- 组件显示状态\n- 回调更新当前选择", latencyMs: 4_200, createdAt: createdAt + 1_000 },
  { id: "gallery-prompt-2", role: "user", content: "给消息列表加入代码和引用示例。", createdAt: createdAt + 60_000 },
  { id: "gallery-answer-2", role: "assistant", content: "代码块支持复制：\n\n```tsx\n<ThreadMessages messages={messages} />\n```\n\n选中这段回复中的文字，可以试用引用操作。", latencyMs: 5_200, createdAt: createdAt + 61_000 },
  { id: "gallery-prompt-3", role: "user", content: "长会话如何跳转到之前的问题？", createdAt: createdAt + 120_000 },
  { id: "gallery-answer-3", role: "assistant", content: "打开问题导航后，可以搜索问题并跳转。\n\n滚动区域左侧的标记也对应每个问题。鼠标停留时会显示问题和回复摘要。", latencyMs: 2_600, createdAt: createdAt + 121_000 },
];

function ComposerPreview() {
  const { t } = useGalleryTranslation();
  const [mode, setMode] = useState<"thread" | "hero" | "streaming" | "disabled">("thread");
  const [preset, setPreset] = useState(presets[0].name);
  const [sent, setSent] = useState<{ kind: "sent"; content: string; attachments: number } | { kind: "stopped" } | null>(null);
  const selected = presets.find((option) => option.name === preset) ?? presets[0];
  return <div className="space-y-4">
    <StatePicker label={t("previews.composer.state")} value={mode} options={["thread", "hero", "streaming", "disabled"]} onChange={setMode} />
    <ThreadComposer disabled={mode === "disabled"} isStreaming={mode === "streaming"} variant={mode === "hero" ? "hero" : "thread"}
      inputAriaLabel={t("previews.composer.input")} placeholder={t("previews.composer.placeholder")}
      modelLabel={selected.name} modelDetail={selected.model} modelProvider={selected.provider} modelPreset={preset}
      modelPresets={presets} onModelPresetChange={setPreset}
      onStop={() => { setMode("thread"); setSent({ kind: "stopped" }); }}
      onSend={(content, attachments) => { setSent({ kind: "sent", content, attachments: attachments?.length ?? 0 }); return true; }} />
    <p role="status" className="text-sm text-muted-foreground">{sent?.kind === "stopped" ? t("previews.composer.stopped")
      : sent ? t("previews.composer.sent", { content: sent.content, attachments: sent.attachments ? t("previews.composer.attachments", { amount: sent.attachments }) : "" })
        : t("previews.composer.hint")}</p>
  </div>;
}

function ModelPresetPreview() {
  const { t } = useTranslation();
  const { t: g } = useGalleryTranslation();
  const [preset, setPreset] = useState(presets[0].name);
  const [mode, setMode] = useState<"ready" | "setup" | "hero">("ready");
  const [action, setAction] = useState("");
  const selected = presets.find((option) => option.name === preset) ?? presets[0];
  return <div className="space-y-4">
    <StatePicker label={g("previews.modelPreset.state")} value={mode} options={["ready", "setup", "hero"]} onChange={setMode} />
    <ModelPresetBadge label={mode === "setup" ? t("thread.composer.configureModel") : selected.name} modelPreset={preset} modelPresets={presets}
      provider={selected.provider} modelDetail={selected.model} isHero={mode === "hero"} needsSetup={mode === "setup"}
      onPresetChange={setPreset} onManageModels={() => setAction("previews.modelPreset.manage")}
      onClick={() => setAction("previews.modelPreset.settings")} />
    <p role="status" className="text-sm text-muted-foreground">{action ? g(action) : g("previews.modelPreset.hint", { preset })}</p>
  </div>;
}

function UsagePreview() {
  const { t } = useGalleryTranslation();
  const [mode, setMode] = useState<"normal" | "caution" | "critical" | "rounds_only" | "sheet">("normal");
  const tokens = mode === "critical" ? 192_000 : mode === "caution" ? 162_000 : 18_500;
  return <div className="space-y-4">
    <StatePicker label={t("previews.usage.state")} value={mode} options={["normal", "caution", "critical", "rounds_only", "sheet"]} onChange={setMode} />
    <ComposerUsagePopover context={mode === "rounds_only" ? null : { contextTokens: tokens, contextWindowTokens: 200_000 }}
      rounds={rounds} showLabel bottomSheet={mode === "sheet"} />
    <p className="text-sm text-muted-foreground">{t("previews.usage.hint")}</p>
  </div>;
}

function WorkspacePreview() {
  const { t } = useTranslation();
  const { t: g } = useGalleryTranslation();
  const [scope, setScope] = useState(projectScope);
  const [mode, setMode] = useState<"ready" | "disabled">("ready");
  const hintBoundaryRef = useRef<HTMLDivElement>(null);
  return <div className="space-y-4">
    <StatePicker label={g("previews.workspace.state")} value={mode} options={["ready", "disabled"]} onChange={setMode} />
    <div ref={hintBoundaryRef} className="flex min-w-0 flex-wrap items-center gap-1">
      <WorkspaceProjectPicker scope={scope} defaultScope={projectScope} controls={workspaceCatalog.controls}
        disabled={mode === "disabled"} onLoadProjects={loadExampleProjects} onChange={setScope} />
      <WorkspaceAccessMenu scope={scope} disabled={mode === "disabled"} onChange={setScope} hintBoundaryRef={hintBoundaryRef} />
    </div>
    <p role="status" className="text-sm text-muted-foreground">{scope.project_path} · {t("thread.composer.workspace.full")}: {t(scope.access_mode === "full" ? "settings.values.on" : "settings.values.off")}</p>
  </div>;
}

function MessagesPreview() {
  const { t } = useGalleryTranslation();
  const [quoted, setQuoted] = useState("");
  const [fork, setFork] = useState<number | null>(null);
  return <div className="space-y-4">
    <div className="max-h-[30rem] overflow-y-auto rounded-control border px-4 py-5">
      <ThreadMessages messages={prompts} onQuoteSelection={setQuoted} onForkFromMessage={setFork} />
    </div>
    <p role="status" className="text-sm text-muted-foreground">{quoted ? t("previews.messages.quoted", { text: quoted }) : fork !== null ? t("previews.messages.forked", { index: fork + 1 }) : t("previews.messages.hint")}</p>
  </div>;
}

function FallbackPreview() {
  const { t } = useGalleryTranslation();
  const [mode, setMode] = useState<"fallback" | "reauth">("fallback");
  const [dismissed, setDismissed] = useState(false);
  const [action, setAction] = useState("");
  return <div className="space-y-4">
    <StatePicker label={t("previews.fallback.state")} value={mode} options={["fallback", "reauth"]} onChange={(value) => { setMode(value); setDismissed(false); }} />
    {!dismissed ? <ModelFallbackNotice model="gpt-4.1" reauthProvider={mode === "reauth" ? "openai" : undefined}
      reauthProviderLabel={mode === "reauth" ? "OpenAI" : undefined} onDismiss={() => setDismissed(true)}
      onOpenSettings={() => setAction("previews.modelPreset.settings")} /> : null}
    <Button variant="outline" size="sm" onClick={() => setDismissed(false)}>{t("previews.fallback.showAgain")}</Button>
    <p role="status" className="text-sm text-muted-foreground">{t(action || "previews.fallback.hint")}</p>
  </div>;
}

function CompactionPreview() {
  const { t } = useGalleryTranslation();
  const [phase, setPhase] = useState<UIContextCompaction["phase"]>("started");
  return <><StatePicker label={t("previews.compaction.phase")} value={phase} options={["started", "succeeded", "failed", "cancelled"]} onChange={setPhase} />
    <ContextCompactionNotice compaction={{ id: "gallery-compaction", phase, announce: true }} /></>;
}


function ClusterPreview() {
  const { t } = useGalleryTranslation();
  const [status, setStatus] = useState<"running" | "done" | "error">("done");
  const [expanded, setExpanded] = useState(true);
  const [startedAt] = useState(Date.now);
  const event: ToolProgressEvent = { call_id: "gallery-read", name: "read_file", arguments: { path: "webui/src/components/thread/ThreadComposer.tsx" },
    phase: status === "running" ? "start" : status === "error" ? "error" : "end", error: status === "error" ? t("previews.cluster.error") : undefined };
  const trace = formatToolCallTrace(event) ?? "read_file()";
  const messages: UIMessage[] = [
    { id: "gallery-reasoning", role: "assistant", content: "", reasoning: "先核对组件接口，再查看消息和模型控件的组合方式。", reasoningStreaming: status === "running", isStreaming: status === "running", createdAt: startedAt },
    { id: "gallery-tool", role: "tool", kind: "trace", content: trace, traces: [trace], toolEvents: [event], createdAt: startedAt + 1_000 },
  ];
  return <><StatePicker label={t("previews.cluster.state")} value={status} options={["running", "done", "error"]} onChange={setStatus} />
    <AgentActivityCluster messages={messages} isTurnStreaming={status === "running"} hasBodyBelow={false}
      startedAtMs={startedAt} turnLatencyMs={4_800} expanded={expanded} onExpandedChange={setExpanded} /></>;
}

function SearchActivityPreview() {
  const { t } = useGalleryTranslation();
  const [status, setStatus] = useState<"running" | "done" | "error">("done");
  const [target, setTarget] = useState<"web" | "x">("web");
  return <><StatePicker label={t("previews.search.state")} value={status} options={["running", "done", "error"]} onChange={setStatus} />
    <StatePicker label={t("previews.search.source")} value={target} options={["web", "x"]} onChange={setTarget} />
    <WebSearchRun turnActive={status === "running"} run={{ key: "gallery-search", query: "React component composition", target, status,
      sources: status === "done" && target === "web" ? [{ title: "React · Passing props to a component", href: "https://react.dev/learn/passing-props-to-a-component", host: "react.dev", displayUrl: "react.dev/learn/passing-props-to-a-component" }] : [] }} /></>;
}

function ResponseSourcesPreview() {
  const { t } = useGalleryTranslation();
  return <Sample label={t("previews.responseSources.hint")}>
    <FallbackResponseSources sources={[
      { preset: "日常", model: "gpt-4.1", provider: "openai", fallback: true },
      { preset: "推理", model: "claude-sonnet-4", provider: "anthropic", fallback: true },
    ]} />
  </Sample>;
}

export const conversationExamples: ComponentExample[] = [
{ title: "previews.composer.title", description: "previews.composer.description", options: "previews.composer.options", files: ["webui/src/components/thread/ThreadComposer.tsx"], Preview: ComposerPreview, code: "<ThreadComposer />" },
{ title: "previews.modelPreset.title", description: "previews.modelPreset.description", options: "previews.modelPreset.options", files: ["webui/src/components/thread/ModelPresetBadge.tsx"], Preview: ModelPresetPreview, code: "<ModelPresetBadge />" },
{ title: "previews.usage.title", description: "previews.usage.description", options: "previews.usage.options", files: ["webui/src/components/thread/ComposerUsagePopover.tsx"], Preview: UsagePreview, code: "<ComposerUsagePopover />" },
{ title: "previews.workspace.title", description: "previews.workspace.description", options: "previews.workspace.options", files: ["webui/src/components/thread/WorkspaceControls.tsx"], Preview: WorkspacePreview, code: "<WorkspaceControls />" },
{ title: "previews.messages.title", description: "previews.messages.description", options: "previews.messages.options", files: ["webui/src/components/thread/ThreadMessages.tsx"], Preview: MessagesPreview, code: "<ThreadMessages />" },
{ title: "previews.fallback.title", description: "previews.fallback.description", options: "previews.fallback.options", files: ["webui/src/components/thread/ModelFallbackNotice.tsx"], Preview: FallbackPreview, code: "<ModelFallbackNotice />" },
{ title: "previews.compaction.title", description: "previews.compaction.description", options: "previews.compaction.options", files: ["webui/src/components/thread/ContextCompactionNotice.tsx"], Preview: CompactionPreview, code: "<ContextCompactionNotice />" },
{ title: "previews.cluster.title", description: "previews.cluster.description", options: "previews.cluster.options", files: ["webui/src/components/thread/AgentActivityCluster.tsx"], Preview: ClusterPreview, code: "<AgentActivityCluster />" },
{ title: "previews.searchActivity.title", description: "previews.searchActivity.description", options: "previews.searchActivity.options", files: ["webui/src/components/thread/activity.tsx"], Preview: SearchActivityPreview, code: "<activity />" },
{ title: "previews.responseSources.title", description: "previews.responseSources.description", options: "previews.responseSources.options", files: ["webui/src/components/ResponseSourceBadge.tsx"], Preview: ResponseSourcesPreview, code: "<ResponseSourceBadge />" }
];
