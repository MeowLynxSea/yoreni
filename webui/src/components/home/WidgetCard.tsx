import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

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
  return (
    <section className={cn(
      "app-surface-glass rounded-[1.75rem] px-6 py-5",
      className,
    )}>
      <header className="flex items-center justify-between gap-3">
        <h2 className="display-serif text-[1.15rem] leading-tight text-foreground/90">{title}</h2>
        {badge}
      </header>
      <div className="mt-4">{children}</div>
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
