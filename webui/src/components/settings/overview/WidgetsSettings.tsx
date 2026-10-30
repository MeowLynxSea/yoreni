import { ChevronDown, ChevronUp, RefreshCw, Trash2 } from "lucide-react";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";

import { HOME_WIDGETS } from "@/components/home/registry";
import { useHomeWidgets } from "@/components/home/widget-layout";
import { SettingsGroup, SettingsRow, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ToggleButton } from "@/components/ui/toggle-button";
import { deleteWidget, refreshWidget } from "@/lib/api";
import type { DynamicWidgetSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const MOVE_BUTTON_CLASS =
  "grid h-7 w-7 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground disabled:pointer-events-none disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none";

export function WidgetsSettings() {
  const { t } = useTranslation();
  const { client } = useClient();
  const { states, setVisible, move } = useHomeWidgets();
  const [deleteTarget, setDeleteTarget] = useState<DynamicWidgetSummary | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");

  const refresh = async (widget: DynamicWidgetSummary) => {
    setBusy(widget.id);
    setActionError("");
    try {
      await refreshWidget(client, widget.id);
    } catch {
      setActionError(t("home.widgets.dynamic.failed"));
    } finally {
      setBusy("");
    }
  };

  const remove = async (widget: DynamicWidgetSummary) => {
    setBusy(widget.id);
    setActionError("");
    try {
      await deleteWidget(client, widget.id);
    } catch {
      setActionError(t("settings.widgets.deleteFailed"));
    } finally {
      setBusy("");
      setDeleteOpen(false);
    }
  };

  return (
    <div className="settings-stack">
      <section>
        <SettingsSectionTitle>{t("settings.widgets.title")}</SettingsSectionTitle>
        <SettingsGroup>
          {states.map((state, index) => {
            const definition = HOME_WIDGETS.find((candidate) => candidate.id === state.id);
            let title: string;
            let description: string;
            let dynamic: DynamicWidgetSummary | null = null;
            if (definition) {
              title = t(definition.titleKey);
              description = t(`settings.widgets.descriptions.${definition.id}`);
            } else if (state.dynamic) {
              dynamic = state.dynamic;
              title = dynamic.title;
              description = dynamic.description || t("settings.widgets.descriptions.dynamic");
            } else {
              return null;
            }
            const working = dynamic !== null && busy === state.id;
            return (
              <Fragment key={state.id}>
                {index ? (
                  <div className="settings-list-inset" aria-hidden="true">
                    <div className="settings-divider" />
                  </div>
                ) : null}
                <SettingsRow title={title} description={description}>
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
                    {dynamic ? (
                      <button
                        type="button"
                        className={MOVE_BUTTON_CLASS}
                        onClick={() => void refresh(dynamic)}
                        disabled={working || !dynamic.has_command}
                        aria-label={t("settings.widgets.refresh", { name: title })}
                      >
                        <RefreshCw
                          aria-hidden
                          className={cn("h-3.5 w-3.5", working && "animate-spin motion-reduce:animate-none")}
                        />
                      </button>
                    ) : null}
                    <ToggleButton
                      checked={state.visible}
                      onChange={(next) => setVisible(state.id, next)}
                      ariaLabel={title}
                      label={t(state.visible ? "settings.values.on" : "settings.values.off")}
                    />
                    {dynamic ? (
                      <button
                        type="button"
                        className={cn(MOVE_BUTTON_CLASS, "hover:text-destructive")}
                        onClick={() => {
                          setDeleteTarget(dynamic);
                          setDeleteOpen(true);
                        }}
                        aria-label={t("settings.widgets.delete", { name: title })}
                      >
                        <Trash2 aria-hidden className="h-3.5 w-3.5" />
                      </button>
                    ) : null}
                  </div>
                </SettingsRow>
              </Fragment>
            );
          })}
        </SettingsGroup>
        {actionError ? (
          <p role="alert" className="settings-list-inset mt-3 text-[12px] leading-5 text-destructive">
            {actionError}
          </p>
        ) : null}
      </section>
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.widgets.deleteTitle", { name: deleteTarget?.title ?? "" })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("settings.widgets.deleteDescription", { name: deleteTarget?.title ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteTarget) void remove(deleteTarget);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {t("settings.widgets.deleteAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
