import { Bell, ChevronRight, LayoutGrid, LogOut } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LucideIcon } from "lucide-react";

import {
  AboutIcon,
  AdvancedIcon,
  AppearanceIcon,
  CapabilitiesIcon,
  ChannelsIcon,
  ModelsIcon,
  OverviewIcon,
  SystemIcon,
} from "@/components/icons/product-icons";
import type { SettingsSectionKey } from "@/components/settings/contracts";

type SettingsEntry = { key: SettingsSectionKey; icon: LucideIcon };

const SETTINGS_GROUPS: SettingsEntry[][] = [
  [
    { key: "overview", icon: OverviewIcon },
    { key: "appearance", icon: AppearanceIcon },
    { key: "widgets", icon: LayoutGrid },
    { key: "notifications", icon: Bell },
    { key: "models", icon: ModelsIcon },
  ],
  [
    { key: "capabilities", icon: CapabilitiesIcon },
    { key: "channels", icon: ChannelsIcon },
    { key: "runtime", icon: SystemIcon },
    { key: "advanced", icon: AdvancedIcon },
    { key: "about", icon: AboutIcon },
  ],
];

interface SettingsIndexProps {
  onOpenSection: (section: SettingsSectionKey) => void;
  onLogout?: () => void;
}

export function SettingsIndex({ onOpenSection, onLogout }: SettingsIndexProps) {
  const { t } = useTranslation();
  const connection = typeof window === "undefined" ? "" : window.location.host;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[34rem] px-5 pb-16 pt-4 sm:pt-8">
        <h1 className="sr-only">{t("settings.sidebar.title")}</h1>
        <div className="flex items-center gap-4 px-1">
          <img
            src="/brand/yoreni_mark.svg"
            alt=""
            draggable={false}
            className="h-14 w-14 shrink-0 select-none rounded-[1.1rem] shadow-[0_10px_30px_-18px_rgba(18,32,55,0.7)]"
          />
          <div className="min-w-0">
            <p className="display-serif truncate text-[1.6rem] leading-tight text-foreground/95">
              yoreni
            </p>
            {connection ? (
              <p className="mt-1 truncate text-[13px] leading-none text-muted-foreground/85">
                {connection}
              </p>
            ) : null}
          </div>
        </div>

        <nav aria-label={t("settings.sidebar.ariaLabel")} className="mt-7 space-y-3">
          {SETTINGS_GROUPS.map((group, index) => (
            <ul
              key={index}
              className="settings-sheet divide-y divide-[color:var(--app-hairline)] overflow-hidden"
            >
              {group.map(({ key, icon: Icon }) => (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() => onOpenSection(key)}
                    className="group flex w-full items-center gap-4 px-6 py-4 text-left transition-colors duration-150 hover:bg-white/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/35 motion-reduce:transition-none dark:hover:bg-white/[0.06]"
                  >
                    <Icon size={20} strokeWidth={1.6} className="shrink-0 text-foreground/70" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-[15.5px] text-foreground/95">
                      {t(`settings.nav.${key}`)}
                    </span>
                    <ChevronRight
                      className="h-4 w-4 shrink-0 text-muted-foreground/55 transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transition-none"
                      aria-hidden
                    />
                  </button>
                </li>
              ))}
            </ul>
          ))}
        </nav>

        {onLogout ? (
          <button
            type="button"
            onClick={onLogout}
            className="settings-sheet mt-3 flex w-full items-center gap-4 px-6 py-4 text-left text-muted-foreground transition-colors duration-150 hover:bg-white/55 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35 motion-reduce:transition-none dark:hover:bg-white/[0.07]"
          >
            <LogOut size={20} strokeWidth={1.6} className="shrink-0" aria-hidden />
            <span className="text-[15.5px]">{t("app.account.logout")}</span>
          </button>
        ) : null}
      </div>
    </div>
  );
}
