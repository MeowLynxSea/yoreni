import { Fragment, type Dispatch, type SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import { HOME_WIDGETS } from "@/components/home/registry";
import { SettingsGroup, SettingsRow, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import { ToggleButton } from "@/components/ui/toggle-button";
import type { LocalPreferences } from "@/lib/local-preferences";

export function WidgetsSettings({ localPrefs, onChangeLocalPrefs }: {
  localPrefs: LocalPreferences;
  onChangeLocalPrefs: Dispatch<SetStateAction<LocalPreferences>>;
}) {
  const { t } = useTranslation();
  return (
    <div className="settings-stack">
      <section>
        <SettingsSectionTitle>{t("settings.widgets.title")}</SettingsSectionTitle>
        <SettingsGroup>
          {HOME_WIDGETS.map((widget, index) => {
            const visible = localPrefs.homeWidgets[widget.id] ?? widget.defaultVisible;
            return (
              <Fragment key={widget.id}>
                {index ? (
                  <div className="settings-list-inset" aria-hidden="true">
                    <div className="border-t border-border/40" />
                  </div>
                ) : null}
                <SettingsRow
                  title={t(widget.titleKey)}
                  description={t(`settings.widgets.descriptions.${widget.id}`)}
                >
                  <ToggleButton
                    checked={visible}
                    onChange={(next) => onChangeLocalPrefs((previous) => ({
                      ...previous,
                      homeWidgets: { ...previous.homeWidgets, [widget.id]: next },
                    }))}
                    ariaLabel={t(widget.titleKey)}
                    label={t(visible ? "settings.values.on" : "settings.values.off")}
                  />
                </SettingsRow>
              </Fragment>
            );
          })}
        </SettingsGroup>
        <p className="settings-list-inset mt-3 text-[12px] leading-5 text-muted-foreground">
          {t("settings.widgets.help")}
        </p>
      </section>
    </div>
  );
}
