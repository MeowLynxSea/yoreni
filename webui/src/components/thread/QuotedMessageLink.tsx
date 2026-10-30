import { useTranslation } from "react-i18next";

import { HoverHint } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface QuotedMessageLinkProps {
  /** The excerpt the message answers. Long quotes are trimmed to one line here. */
  quote: string;
  /** Conversation row the excerpt came from, when known. */
  targetId?: string | null;
  onJump?: (targetId: string | null, quote: string) => void;
  /** Side of the message the quote hugs: the Agent's left, the reader's right. */
  align?: "start" | "end";
  className?: string;
}

/**
 * The message a reply answers, kept as one trimmed line above it. The full
 * excerpt stays in the tooltip so long passages never crowd the transcript,
 * and the line itself jumps back to the original.
 */
export function QuotedMessageLink({
  quote,
  targetId = null,
  onJump,
  align = "start",
  className,
}: QuotedMessageLinkProps) {
  const { t } = useTranslation();
  const excerpt = quote.replace(/\s+/g, " ").trim();
  return (
    <HoverHint
      side="top"
      align={align === "end" ? "end" : "start"}
      contentClassName="max-w-[min(24rem,calc(100vw-2rem))] px-3 py-2"
      content={
        <>
          <span className="block max-h-[5.5rem] overflow-hidden whitespace-pre-wrap break-words text-[12px] leading-5">
            {quote}
          </span>
          <span className="mt-1 block text-[11px] text-muted-foreground">
            {t("message.jumpToQuoted")}
          </span>
        </>
      }
    >
      <button
        type="button"
        data-message-quote-link
        aria-label={t("message.quotedMessage")}
        disabled={!onJump}
        onClick={() => onJump?.(targetId, quote)}
        className={cn(
          "group/quote inline-flex max-w-full min-w-0 items-center gap-1.5",
          "text-[11px] leading-4 text-muted-foreground/80",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          align === "end" && "flex-row-reverse",
          className,
        )}
      >
        <svg
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground/55 transition-colors",
            "group-hover/quote:text-foreground",
            align === "end" && "-scale-x-100",
          )}
        >
          <path
            d="M4.5 18.5V10a5.5 5.5 0 0 1 5.5-5.5h7.5"
            stroke="currentColor"
            strokeWidth="2.75"
            strokeLinecap="round"
          />
        </svg>
        <span
          data-message-quote-excerpt
          className={cn(
            "min-w-0 truncate rounded-control border border-border/60 bg-muted/25 px-2 py-0.5",
            "transition-colors",
            "group-hover/quote:border-border group-hover/quote:bg-muted/40 group-hover/quote:text-foreground",
          )}
        >
          {excerpt}
        </span>
      </button>
    </HoverHint>
  );
}
