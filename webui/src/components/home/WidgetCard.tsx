import { ChevronDown } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { useWidgetFrame } from "./WidgetFrame";

export function WidgetCard({
  title,
  badge,
  children,
  className,
}: {
  title: string;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const frame = useWidgetFrame();
  const contentId = useId();
  const collapsible = frame?.collapsible === true;
  const collapsed = collapsible && frame.collapsed;

  return (
    <section className={cn(
      "app-surface-glass rounded-[1.75rem] px-6 py-5",
      className,
    )}>
      <header className="flex items-center justify-between gap-3">
        {collapsible ? (
          <button
            type="button"
            onClick={frame.onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls={contentId}
            className="group -mx-1 flex min-w-0 items-center gap-1.5 rounded-full px-1 text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            <h2 className="display-serif truncate text-[1.15rem] leading-tight text-foreground/90">{title}</h2>
            <ChevronDown
              aria-hidden
              className={cn(
                "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none",
                collapsed ? "-rotate-90" : "rotate-0",
              )}
            />
          </button>
        ) : (
          <h2 className="display-serif text-[1.15rem] leading-tight text-foreground/90">{title}</h2>
        )}
        {badge}
      </header>
      <div
        id={contentId}
        aria-hidden={collapsed}
        {...(collapsed ? { inert: "" } : {})}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
          collapsed ? "grid-rows-[0fr] opacity-0" : "grid-rows-[1fr] opacity-100",
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="pt-4">{children}</div>
        </div>
      </div>
    </section>
  );
}

export function WidgetPill({ tone, children }: { tone: "active" | "blocked" | "muted"; children: ReactNode }) {
  return (
    <span className={cn(
      "shrink-0 rounded-full px-2.5 py-1 text-[11px] leading-4",
      tone === "active" && "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
      tone === "blocked" && "bg-amber-500/14 text-amber-700 dark:text-amber-300",
      tone === "muted" && "bg-foreground/[0.06] text-muted-foreground",
    )}>
      {children}
    </span>
  );
}
