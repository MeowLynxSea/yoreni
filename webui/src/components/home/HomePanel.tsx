import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { HOME_WIDGETS } from "./registry";
import { DynamicWidget } from "./DynamicWidget";
import { WidgetFrame } from "./WidgetFrame";
import { useHomeWidgets } from "./widget-layout";

export function HomeFeed({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { states, toggleCollapsed } = useHomeWidgets();
  const [present, setPresent] = useState<Record<string, boolean>>({});

  const reportPresence = useCallback((id: string, visible: boolean) => {
    setPresent((previous) => (previous[id] === visible ? previous : { ...previous, [id]: visible }));
  }, []);

  const visible = states.filter((state) => state.visible);
  const empty = !visible.some((state) => present[state.id]);

  return (
    <div className="flex flex-1 flex-col">
      <div className={cn("-mt-3 flex flex-col", className)}>
        {visible.map((state) => {
          const definition = HOME_WIDGETS.find((widget) => widget.id === state.id);
          if (!definition && !state.dynamic) return null;
          return (
            <WidgetFrame
              key={state.id}
              id={state.id}
              collapsible={state.collapsible}
              collapsed={state.collapsed}
              onToggleCollapsed={() => toggleCollapsed(state.id)}
              onPresenceChange={reportPresence}
            >
              {definition
                ? <definition.Component />
                : state.dynamic
                  ? <DynamicWidget id={state.id} summary={state.dynamic} />
                  : null}
            </WidgetFrame>
          );
        })}
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
