import { useRef, useState } from "react";

import { StarLinkView, StarPromptView } from "@/components/StarPromptView";
import { AboutSettingsView } from "@/components/settings/overview/AboutSettingsView";
import { AppearanceSettingsView } from "@/components/settings/overview/AppearanceSettingsView";
import { AutomationRunDialogView, type AutomationRunResult } from "@/components/settings/system/AutomationRunDialogView";
import { Button } from "@/components/ui/button";
import { DEFAULT_LOCAL_PREFS } from "@/lib/local-preferences";
import type { ThemePreference } from "@/hooks/useTheme";
import type { AutomationJob } from "@/lib/types";
import { StatePicker, type ComponentExample } from "./example";
import { useGalleryTranslation } from "./i18n";

const job: AutomationJob = {
  id: "gallery-report", name: "Weekly summary", enabled: true,
  schedule: { kind: "cron", expr: "30 9 * * 1", tz: "UTC" },
  payload: { message: "Summarize the changes and open questions." }, state: {},
  origin: { channel: "websocket", title: "Planning" },
};

function RunResultPreview() {
  const { t, i18n } = useGalleryTranslation();
  const [mode, setMode] = useState<"complete" | "loading" | "error" | "empty" | "unavailable">("complete");
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const result: AutomationRunResult = mode === "loading" || mode === "error"
    ? { status: mode, response: null }
    : { status: "loaded", response: mode === "empty" ? "" : mode === "unavailable" ? null : "## Weekly summary\n\n- **Completed:** component previews\n- **Next:** review the changes" };
  return <><StatePicker label={t("controls.runResult.state")} value={mode} options={["complete", "loading", "error", "empty", "unavailable"]} onChange={setMode} />
    <Button ref={trigger} variant="outline" onClick={() => setOpen(true)}>{t("controls.runResult.view")}</Button>
    <AutomationRunDialogView job={job} run={{ run_at_ms: Date.UTC(2026, 9, 12, 9, 30), status: "ok", duration_ms: 3200 }}
      locale={i18n.language} open={open} result={result} onRetry={() => setMode("complete")} onOpenChange={setOpen}
      onCloseAutoFocus={(event) => { event.preventDefault(); trigger.current?.focus(); }} /></>;
}

function AboutPreview() {
  const { t } = useGalleryTranslation();
  const [notice, setNotice] = useState<"selectedLink" | null>(null);
  return <><AboutSettingsView reportIssueUrl="https://github.com/MeowLynxSea/yoreni/issues/new?template=bug_report.yml"
    version={<div className="settings-list-row flex items-center gap-3 text-[14px]">
      <span className="flex-1">{t("controls.about.version")}</span>
      <span className="text-muted-foreground">0.3.5 · abcdef1</span>
    </div>}
    starLink={<StarLinkView onActivate={() => setNotice("selectedLink")} />} />
    {notice ? <p role="status" className="mt-4 text-xs text-muted-foreground">{t(`controls.about.${notice}`)}</p> : null}</>;
}

function AppearancePreview() {
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [preferences, setPreferences] = useState(DEFAULT_LOCAL_PREFS);
  return <><AppearanceSettingsView themePreference={theme} onChangeTheme={setTheme}
    localPrefs={preferences} onChangeLocalPrefs={setPreferences} />
    <pre role="status" className="mt-4 overflow-auto rounded-control bg-muted p-4 text-xs">{JSON.stringify({ theme, ...preferences }, null, 2)}</pre></>;
}

function StarInvitationPreview() {
  const { t } = useGalleryTranslation();
  const [mode, setMode] = useState<"ready" | "saving" | "error">("ready");
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<"dismissed" | "starred" | null>(null);
  return <><StatePicker label={t("controls.starInvitation.state")} value={mode} options={["ready", "saving", "error"]} onChange={setMode} />
    <Button variant="outline" onClick={() => setOpen(true)}>{t("controls.starInvitation.view")}</Button>
    <StarPromptView open={open} saving={mode === "saving"} error={mode === "error"} onOpenChange={setOpen}
      onDismissForever={() => { setNotice("dismissed"); setOpen(false); }}
      onStar={() => { setNotice("starred"); setOpen(false); }} />
    {notice ? <p role="status" className="mt-4 text-xs text-muted-foreground">{t(`controls.starInvitation.${notice}`)}</p> : null}</>;
}

export const runtimeSettingsExamples: ComponentExample[] = [
{ title: "controls.runResult.title", files: ["webui/src/components/settings/system/AutomationRunDialogView.tsx"], Preview: RunResultPreview,
    description: "controls.runResult.description",
    options: "controls.runResult.options",
    code: '<AutomationRunDialogView job={job} run={run} locale="zh-CN" open={open} result={{ status: "loaded", response }} onRetry={retry} onOpenChange={setOpen} onCloseAutoFocus={restoreFocus} />' },
{ title: "controls.about.title", files: ["webui/src/components/settings/overview/AboutSettingsView.tsx"], Preview: AboutPreview,
    description: "controls.about.description",
    options: "controls.about.options",
    code: '<AboutSettingsView reportIssueUrl={reportIssueUrl} version={<VersionRow currentVersion={version} />} starLink={<StarLinkView onActivate={star} />} />' },
{ title: "controls.appearance.title", files: ["webui/src/components/settings/overview/AppearanceSettingsView.tsx"], Preview: AppearancePreview,
    description: "controls.appearance.description",
    options: "controls.appearance.options",
    code: '<AppearanceSettingsView themePreference={preference} onChangeTheme={setTheme} localPrefs={preferences} onChangeLocalPrefs={setPreferences} />' },
{ title: "controls.starInvitation.title", files: ["webui/src/components/StarPromptView.tsx"], Preview: StarInvitationPreview,
    description: "controls.starInvitation.description",
    options: "controls.starInvitation.options",
    code: '<StarPromptView open={open} saving={saving} error={error} onOpenChange={setOpen} onDismissForever={dismiss} onStar={openRepository} />' }
];
