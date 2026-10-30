import { ChevronDown, ChevronUp } from "lucide-react";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";

import { HOME_WIDGETS } from "@/components/home/registry";
import { useHomeWidgets } from "@/components/home/widget-layout";
import { SettingsGroup, SettingsRow, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import { ToggleButton } from "@/components/ui/toggle-button";

const MOVE_BUTTON_CLASS =
  "grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground disabled:pointer-events-none disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none";

export function WidgetsSettings() {
  const { t } = useTranslation();
  const { states, setVisible, move } = useHomeWidgets();

  return (
    <div className="settings-stack">
      <section>
        <SettingsSectionTitle>{t("settings.widgets.title")}</SettingsSectionTitle>
        <SettingsGroup>
          {states.map((state, index) => {
            const widget = HOME_WIDGETS.find((candidate) => candidate.id === state.id);
            if (!widget) return null;
            const title = t(widget.titleKey);
            return (
              <Fragment key={state.id}>
                {index ? (
                  <div className="settings-list-inset" aria-hidden="true">
                    <div className="settings-divider" />
                  </div>
                ) : null}
                <SettingsRow
                  title={title}
                  description={t(`settings.widgets.descriptions.${widget.id}`)}
                >
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-0.5">
                      <button
                        type="button"
                        className={MOVE_BUTTON_CLASS}
                        onClick={() => move(state.id, -1)}
                        disabled={index === 0}
                        aria-label={t("settings.widgets.moveUp", { name: title })}
                      >
                        <ChevronUp aria-hidden className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        className={MOVE_BUTTON_CLASS}
                        onClick={() => move(state.id, 1)}
                        disabled={index === states.length - 1}
                        aria-label={t("settings.widgets.moveDown", { name: title })}
                      >
                        <ChevronDown aria-hidden className="h-4 w-4" />
                      </button>
                    </div>
                    <ToggleButton
                      checked={state.visible}
                      onChange={(next) => setVisible(state.id, next)}
                      ariaLabel={title}
                      label={t(state.visible ? "settings.values.on" : "settings.values.off")}
                    />
                  </div>
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
