import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggleIcon } from "@/components/icons/ThemeToggleIcon";
import { cn } from "@/lib/utils";

type AppHeaderProps = {
  theme: "light" | "dark";
  onToggleTheme: () => void;
};

export function AppHeader({ theme, onToggleTheme }: AppHeaderProps) {
  const { t } = useTranslation();
  const settingsLabel = t("settings.sidebar.title", { defaultValue: "Settings" });
  const preloadSettings = () => { void import("@/components/settings/SettingsView"); };

  return (
    <header className="relative z-30 flex h-16 shrink-0 items-center gap-2 px-4 sm:px-6">
      <NavLink
        to="/"
        end
        viewTransition
        aria-label="yoreni"
        className="flex shrink-0 items-center gap-2.5 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2"
      >
        <img
          src="/brand/yoreni_mark.svg"
          alt=""
          draggable={false}
          className="h-8 w-8 select-none rounded-[0.65rem] shadow-[0_1px_0_rgba(255,255,255,0.35)]"
        />
        <span className="display-serif hidden text-[1.45rem] leading-none text-foreground sm:block">
          yoreni
        </span>
      </NavLink>

      <div className="ml-auto flex items-center gap-0.5">
        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={t("settings.nav.appearance")}
          title={t("settings.nav.appearance")}
          className="grid h-9 w-9 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:hover:bg-white/[0.06]"
        >
          <ThemeToggleIcon theme={theme} />
        </button>
        <LanguageSwitcher collapsed className="hidden text-muted-foreground sm:flex" />
        <NavLink
          to="/settings"
          viewTransition
          aria-label={settingsLabel}
          onMouseEnter={preloadSettings}
          onFocus={preloadSettings}
          className={({ isActive }) => cn(
            "ml-0.5 grid h-9 w-9 place-items-center overflow-hidden rounded-full border border-white/60 bg-white/40 transition-[opacity,box-shadow] hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:border-white/10 dark:bg-white/[0.06]",
            isActive && "ring-2 ring-foreground/15",
          )}
        >
          <img src="/brand/yoreni_mark.svg" alt="" draggable={false} className="h-7 w-7 object-cover" />
        </NavLink>
      </div>
    </header>
  );
}
