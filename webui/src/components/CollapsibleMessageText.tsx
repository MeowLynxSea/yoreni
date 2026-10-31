import { ChevronDown } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Keep a wall of text readable without hiding the beginning of the answer. */
const COLLAPSED_MAX_HEIGHT = "max-h-[26rem]";

const MASK_CLASS = "[mask-image:linear-gradient(to_bottom,black_72%,transparent)]";

const INTERACTIVE_SELECTOR = "a,button,input,textarea,select,label,[role='button'],[contenteditable='true']";

/**
 * Clip one chat message to a readable height, and open the whole text in a
 * reader on click. The clip is pure CSS; whether it applies is decided from the
 * laid-out height, so a short message is never faded and a long one is never
 * left open just because it happens to be made of few characters.
 */
export function CollapsibleMessageText({
  children,
  fullContent,
  streaming = false,
  className,
}: {
  children: ReactNode;
  /** The reader's copy of the message; defaults to the same node as the preview. */
  fullContent?: ReactNode;
  streaming?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const clipRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  const [open, setOpen] = useState(false);

  const measure = useCallback(() => {
    const clip = clipRef.current;
    const content = contentRef.current;
    if (!clip || !content) return;
    const limit = clip.clientHeight;
    setClipped(limit > 0 && content.scrollHeight > limit + 1);
  }, []);

  useLayoutEffect(() => {
    if (streaming) {
      setClipped(false);
      return;
    }
    measure();
  }, [measure, streaming, children]);

  useEffect(() => {
    if (streaming || typeof ResizeObserver === "undefined") return;
    const clip = clipRef.current;
    const content = contentRef.current;
    if (!clip || !content) return;
    // Images and fonts settle after mount, so follow both the text height and
    // the width the clip is laid out in.
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    observer.observe(clip);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure, streaming]);

  const collapsed = clipped && !streaming;

  /** Expand from anywhere in the preview except a link, a control, or a selection. */
  const reveal = (event: MouseEvent<HTMLDivElement>) => {
    if (!collapsed) return;
    const target = event.target as Element | null;
    if (target?.closest(INTERACTIVE_SELECTOR)) return;
    if (window.getSelection()?.toString()) return;
    setOpen(true);
  };

  const label = t("message.readFullMessage");

  return (
    <>
      <div
        ref={clipRef}
        data-message-clip={streaming ? undefined : "true"}
        data-message-trimmed={collapsed ? "true" : undefined}
        onClick={reveal}
        className={cn("relative", !streaming && "overflow-hidden", !streaming && COLLAPSED_MAX_HEIGHT,
          collapsed && MASK_CLASS, className)}
      >
        <div ref={contentRef} data-message-content={streaming ? undefined : "true"}>{children}</div>
      </div>
      {collapsed ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={cn(
            "mt-2 inline-flex items-center gap-1 rounded-control text-[13px] font-medium",
            "text-muted-foreground transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          {label}
          <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
        </button>
      ) : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          aria-describedby={undefined}
          className="max-h-[85dvh] w-[min(92vw,46rem)] overflow-y-auto overscroll-contain"
        >
          <DialogTitle>{t("message.fullMessageTitle")}</DialogTitle>
          <div data-message-reader className="text-reading">{fullContent ?? children}</div>
        </DialogContent>
      </Dialog>
    </>
  );
}
