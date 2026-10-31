import { useEditCompletion, type EditCompletion } from "@/components/settings/shared/useEditCompletion";
import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import {
  RestartSettingsFooter,
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
} from "@/components/settings/shared/SettingsControls";
import { ToggleButton } from "@/components/ui/toggle-button";
import type {
  NetworkSafetySettingsUpdate,
  SettingsPayload,
} from "@/lib/types";

export const DEFAULT_NETWORK_SAFETY_FORM: NetworkSafetySettingsUpdate = {
  webuiAllowLocalServiceAccess: true,
};

export function networkSafetyFormFromPayload(payload: SettingsPayload): NetworkSafetySettingsUpdate {
  return {
    webuiAllowLocalServiceAccess:
      payload.advanced.webui_allow_local_service_access ??
      payload.advanced.allow_local_preview_access ??
      true,
  };
}

export function AdvancedSettings({
  editCompletion,
  error,
  form,
  dirty,
  saving,
  requiresRestartPending,
  isNativeHostSurface,
  onChangeForm,
  onSave,
  onRestart,
  isRestarting,
}: {
  editCompletion?: EditCompletion;
  error?: string;
  form: NetworkSafetySettingsUpdate;
  dirty: boolean;
  saving: boolean;
  requiresRestartPending: boolean;
  isNativeHostSurface: boolean;
  onChangeForm: Dispatch<SetStateAction<NetworkSafetySettingsUpdate>>;
  onSave: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
}) {
  const { t } = useTranslation();
  const localEdits = useEditCompletion(form, dirty, onSave, !editCompletion);
  const edits = editCompletion ?? localEdits;
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  return (
    <div className="settings-stack" onChangeCapture={edits.onChangeCapture} onFocusCapture={edits.onFocusCapture} onBlurCapture={edits.onBlurCapture}>
      <section>
        <SettingsSectionTitle>
          {isNativeHostSurface
            ? tx("settings.sections.hostSafety", "App safety")
            : tx("settings.sections.webuiSafety", "Web safety")}
        </SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={tx("settings.rows.localServiceAccess", "Local Service Access")}
            description={tx(
              isNativeHostSurface ? "settings.help.localServiceAccessNative" : "settings.help.localServiceAccess",
              isNativeHostSurface
                ? "Allow Full Access shell commands to reach services on this Mac."
                : "Allow Full Access shell commands to reach localhost services.",
            )}
          >
            <ToggleButton
              checked={form.webuiAllowLocalServiceAccess}
              onChange={(webuiAllowLocalServiceAccess) =>
                onChangeForm((prev) => ({ ...prev, webuiAllowLocalServiceAccess }))
              }
              ariaLabel={tx("settings.rows.localServiceAccess", "Local Service Access")}
              label={form.webuiAllowLocalServiceAccess ? tx("settings.values.on", "On") : tx("settings.values.off", "Off")}
            />
          </SettingsRow>
          <RestartSettingsFooter
            autoSave
            error={Boolean(error)}
            message={error}
            dirty={dirty}
            saving={saving}
            pendingRestart={requiresRestartPending}
            onSave={onSave}
            onRestart={onRestart}
            isRestarting={isRestarting}
          />
        </SettingsGroup>
      </section>

    </div>
  );
}
