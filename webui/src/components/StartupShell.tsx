import { useTranslation } from "react-i18next";

import { readStartupLayout, StartupSkeleton, type StartupSurface } from "./StartupSkeleton";

export function StartupShell({ embedded = false, surface }: {
  embedded?: boolean;
  surface?: StartupSurface;
}) {
  const { t } = useTranslation();
  const layout = readStartupLayout();
  return <StartupSkeleton {...layout} embedded={embedded} surface={surface ?? layout.surface} label={t("app.loading.boot")} />;
}
