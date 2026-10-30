import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SettingsExitGuard, SettingsSectionKey } from "@/components/settings/contracts";
import { useSettingsController } from "@/components/settings/useSettingsController";
import type { SendAttachment, SendOptions } from "@/lib/agent-input";
import type { ThemePreference } from "@/hooks/useTheme";
import type { SettingsPayload, SkillSummary } from "@/lib/types";

export type { SettingsSectionKey } from "@/components/settings/contracts";

interface SettingsViewProps {
  registerExitGuard?: (guard: SettingsExitGuard | null) => void;
  themePreference: ThemePreference;
  initialSection?: SettingsSectionKey;
  initialSettings?: SettingsPayload | null;
  showSidebar?: boolean;
  mainNavigationExpanded?: boolean;
  onChangeTheme: (value: ThemePreference) => void;
  onBackToChat: () => void;
  onBack?: () => void;
  backLabel?: string;
  onModelNameChange: (modelName: string | null) => void;
  onSettingsChange?: (payload: SettingsPayload) => void;
  skills?: SkillSummary[];
  skillsLoading?: boolean;
  skillsError?: boolean;
  onStartAutomationChat?: (
    content: string,
    images?: SendAttachment[],
    options?: SendOptions,
    modelPreset?: string | null,
  ) => boolean | void | Promise<boolean | void>;
  titleOverrides?: Record<string, string>;
  onSectionChange?: (section: SettingsSectionKey) => void;
  onLogout?: () => void;
  onRestart?: () => void;
  onNativeEngineRestart?: () => Promise<string>;
  isRestarting?: boolean;
  hostChromeInset?: boolean;
}

export function SettingsView({
  registerExitGuard,
  themePreference,
  initialSection = "overview",
  initialSettings = null,
  showSidebar = true,
  mainNavigationExpanded = false,
  onChangeTheme,
  onBackToChat,
  onBack,
  backLabel,
  onModelNameChange,
  onSettingsChange,
  skills = [],
  skillsLoading = false,
  skillsError = false,
  onStartAutomationChat,
  titleOverrides,
  onSectionChange,
  onLogout,
  onRestart,
  onNativeEngineRestart,
  isRestarting = false,
  hostChromeInset = false,
}: SettingsViewProps) {
  const controller = useSettingsController({
    initialSection,
    initialSettings,
    onModelNameChange,
    onSettingsChange,
    onSectionChange,
    onRestart,
    onNativeEngineRestart,
  });

  return (
    <SettingsPage
      registerExitGuard={registerExitGuard}
      controller={controller}
      themePreference={themePreference}
      showSidebar={showSidebar}
      mainNavigationExpanded={mainNavigationExpanded}
      onChangeTheme={onChangeTheme}
      onBackToChat={onBackToChat}
      onBack={onBack}
      backLabel={backLabel}
      skills={skills}
      skillsLoading={skillsLoading}
      skillsError={skillsError}
      onStartAutomationChat={onStartAutomationChat}
      titleOverrides={titleOverrides}
      onLogout={onLogout}
      isRestarting={isRestarting}
      hostChromeInset={hostChromeInset}
    />
  );
}
