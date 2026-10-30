import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useLocalPreferences } from "@/hooks/useLocalPreferences";
import { cn } from "@/lib/utils";

import { HOME_WIDGETS } from "./registry";

function visibleWidgets(hidden: Record<string, boolean>) {
  return HOME_WIDGETS.filter((widget) => hidden[widget.id] ?? widget.defaultVisible);
}

export function HomeFeed({ className }: { className?: string }) {
  const { t } = useTranslation();
  const prefs = useLocalPreferences();
  const widgets = visibleWidgets(prefs.homeWidgets);

  if (!widgets.length) {
    return <p className={cn("px-1 text-[13px] leading-6 text-muted-foreground", className)}>{t("home.empty")}</p>;
  }
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {widgets.map(({ id, Component }) => <Component key={id} />)}
    </div>
  );
}

export function HomePanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    const node = panel.current;
    if (!node) return;
    if (open) node.removeAttribute("inert");
    else node.setAttribute("inert", "");
  }, [open]);

  return (
    <>
      <div
        aria-hidden
        onClick={onClose}
        className={cn(
          "absolute inset-0 z-20 bg-background/30 backdrop-blur-[2px] transition-opacity duration-300 md:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />
      <aside
        ref={panel}
        aria-label={t("home.title")}
        data-state={open ? "open" : "closed"}
        className={cn(
          "absolute inset-y-0 left-0 z-30 w-[min(21rem,85vw)] transition-[transform,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
          "md:relative md:inset-auto md:z-auto md:shrink-0 md:overflow-hidden md:transition-[width,opacity]",
          open ? "translate-x-0 opacity-100 md:w-[21rem]" : "-translate-x-full opacity-0 md:w-0 md:translate-x-0",
        )}
      >
        <div className="flex h-full w-[min(21rem,85vw)] flex-col bg-white/[0.08] backdrop-blur-2xl dark:bg-white/[0.015] md:w-[21rem]">
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-10 pt-20 md:px-5">
            <HomeFeed />
          </div>
        </div>
      </aside>
    </>
  );
}
