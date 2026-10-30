import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";

import { useLocalPreferences } from "@/hooks/useLocalPreferences";
import { cn } from "@/lib/utils";

import { HOME_WIDGETS } from "./registry";

function visibleWidgets(hidden: Record<string, boolean>) {
  return HOME_WIDGETS.filter((widget) => hidden[widget.id] ?? widget.defaultVisible);
}

function useRefEmpty(node: RefObject<HTMLDivElement | null>) {
  const [empty, setEmpty] = useState(true);
  useLayoutEffect(() => {
    const element = node.current;
    if (!element) return;
    const update = () => setEmpty(element.childElementCount === 0);
    update();
    const observer = new MutationObserver(update);
    observer.observe(element, { childList: true });
    return () => observer.disconnect();
  }, [node]);
  return empty;
}

export function HomeFeed({ className }: { className?: string }) {
  const { t } = useTranslation();
  const prefs = useLocalPreferences();
  const widgets = visibleWidgets(prefs.homeWidgets);
  const feed = useRef<HTMLDivElement>(null);
  const empty = useRefEmpty(feed);

  return (
    <div className="flex flex-1 flex-col">
      <div ref={feed} className={cn("flex flex-col gap-3", className)}>
        {widgets.map(({ id, Component }) => <Component key={id} />)}
      </div>
      {empty ? (
        <div className="flex flex-1 flex-col items-center justify-center px-4 pb-12 text-center">
          <p className="text-[15px] font-medium leading-6 text-foreground/85">{t("home.empty.title")}</p>
          <p className="mt-1.5 text-[13px] leading-6 text-muted-foreground">{t("home.empty.description")}</p>
        </div>
      ) : null}
    </div>
  );
}

export function HomePanel({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    const node = panel.current;
    if (!node) return;
    if (open) node.removeAttribute("inert");
    else node.setAttribute("inert", "");
  }, [open]);

  return (
    <aside
      ref={panel}
      aria-label={t("home.title")}
      data-state={open ? "open" : "closed"}
      className={cn(
        "w-full md:relative md:z-auto md:shrink-0 md:overflow-hidden md:transition-[width,opacity] md:duration-300 md:[transition-timing-function:cubic-bezier(0.22,1,0.36,1)] md:motion-reduce:transition-none",
        open ? "flex md:w-[21rem] md:opacity-100" : "hidden md:flex md:w-0 md:opacity-0",
      )}
    >
      <div className="flex h-full w-full flex-col bg-white/[0.08] backdrop-blur-2xl dark:bg-white/[0.015] md:w-[21rem]">
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-4 pb-10 pt-20 md:px-5">
          <HomeFeed />
        </div>
      </div>
    </aside>
  );
}
