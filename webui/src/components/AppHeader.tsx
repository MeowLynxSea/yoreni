import { House, Search } from "lucide-react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggleIcon } from "@/components/icons/ThemeToggleIcon";
import type { HomeView } from "@/lib/local-ui-state";
import { cn } from "@/lib/utils";

type AppHeaderProps = {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onOpenSearch: () => void;
  homeView?: HomeView;
  onHomeViewChange?: (view: HomeView) => void;
};

export function AppHeader({
  theme,
  onToggleTheme,
  onOpenSearch,
  homeView,
  onHomeViewChange,
}: AppHeaderProps) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const settingsLabel = t("settings.sidebar.title", { defaultValue: "Settings" });
  const preloadSettings = () => { void import("@/components/settings/SettingsView"); };
  const onHome = location.pathname === "/" && homeView === "home";
  const onRoute = location.pathname === "/";
  const openHome = () => {
    if (location.pathname !== "/") {
      onHomeViewChange?.("home");
      navigate("/", { viewTransition: true });
      return;
    }
    onHomeViewChange?.(onHome ? "chat" : "home");
  };
  // The pill doubles as the way out of settings on mobile.
  const openHomeView = (view: HomeView) => {
    onHomeViewChange?.(view);
    if (location.pathname !== "/") navigate("/", { viewTransition: true });
  };

  return (
    <header className="pointer-events-none absolute inset-x-0 top-0 z-40 flex h-16 items-center gap-2 px-4 sm:px-6">
      <button
        type="button"
        onClick={openHome}
        aria-pressed={onHome}
        aria-label={t("home.title")}
        className={cn(
          "pointer-events-auto hidden h-11 w-11 shrink-0 place-items-center rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 md:grid",
          "border-white/60 bg-white/40 text-foreground/85 hover:bg-white/60 dark:border-white/10 dark:bg-white/[0.06] dark:hover:bg-white/[0.1]",
          onHome && "ring-2 ring-foreground/15",
        )}
      >
        <House className="h-5 w-5" strokeWidth={1.7} aria-hidden />
      </button>

      <div className="pointer-events-auto absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded-full border border-white/60 bg-white/45 p-1 backdrop-blur-xl md:hidden dark:border-white/10 dark:bg-white/[0.06]">
        <button
          type="button"
          onClick={() => openHomeView("home")}
          aria-pressed={onRoute && homeView === "home"}
          className={cn(
            "h-10 rounded-full px-4 text-[14px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            onRoute && homeView === "home"
              ? "bg-white text-foreground shadow-sm dark:bg-white/[0.14]"
              : "text-muted-foreground",
          )}
        >
          {t("home.title")}
        </button>
        <button
          type="button"
          onClick={() => openHomeView("chat")}
          aria-pressed={onRoute && homeView !== "home"}
          className={cn(
            "h-10 rounded-full px-4 text-[14px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            onRoute && homeView !== "home"
              ? "bg-white text-foreground shadow-sm dark:bg-white/[0.14]"
              : "text-muted-foreground",
          )}
        >
          {t("home.tabs.chat")}
        </button>
      </div>

      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          onClick={onOpenSearch}
          aria-label={t("search.open")}
          title={t("search.open")}
          className="pointer-events-auto grid h-11 w-11 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 max-md:absolute max-md:left-4 max-md:top-1/2 max-md:-translate-y-1/2 md:static md:translate-y-0 dark:hover:bg-white/[0.06]"
        >
          <Search className="h-5 w-5" strokeWidth={1.7} aria-hidden />
        </button>
        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={t("settings.nav.appearance")}
          title={t("settings.nav.appearance")}
          className="pointer-events-auto grid h-11 w-11 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:hover:bg-white/[0.06]"
        >
          <ThemeToggleIcon theme={theme} className="h-5 w-5" />
        </button>
        <LanguageSwitcher collapsed className="pointer-events-auto hidden h-11 w-11 text-muted-foreground sm:flex" />
        <NavLink
          to="/settings"
          viewTransition
          aria-label={settingsLabel}
          onMouseEnter={preloadSettings}
          onFocus={preloadSettings}
          className={({ isActive }) => cn(
            "pointer-events-auto ml-0.5 grid h-11 w-11 place-items-center overflow-hidden rounded-full border border-white/60 bg-white/40 transition-[opacity,box-shadow] hover:opacity-85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:border-white/10 dark:bg-white/[0.06]",
            isActive && "ring-2 ring-foreground/15",
          )}
        >
          <img src="/brand/yoreni_mark.svg" alt="" draggable={false} className="h-8 w-8 object-cover" />
        </NavLink>
      </div>
    </header>
  );
}
