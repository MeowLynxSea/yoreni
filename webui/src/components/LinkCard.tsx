import { ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import type { UIMediaAttachment } from "@/lib/types";

function siteOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** A link the Agent handed over: the page its title and description, not a bare URL. */
export function LinkCard({ link, className }: { link: UIMediaAttachment; className?: string }) {
  const href = link.url;
  if (!href) return null;
  const site = link.site || siteOf(href);
  const title = link.title || link.name || site || href;
  return (
    <a
      data-link-card
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "not-prose flex w-full min-w-0 flex-col gap-1 rounded-control",
        "border border-border/60 bg-card/45 px-3.5 py-3",
        "transition-colors hover:border-border hover:bg-card/70",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      {site ? (
        <span className="truncate text-[11px] font-medium leading-4 text-muted-foreground">{site}</span>
      ) : null}
      <span className="flex min-w-0 items-start gap-1.5">
        <span className="line-clamp-2 min-w-0 [overflow-wrap:anywhere] text-[14px] font-medium leading-5 text-foreground">
          {title}
        </span>
        <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden strokeWidth={1.75} />
      </span>
      {link.description ? (
        <span className="line-clamp-2 text-[12px] leading-5 text-muted-foreground">{link.description}</span>
      ) : null}
    </a>
  );
}
