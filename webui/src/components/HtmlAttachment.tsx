import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, ExternalLink } from "lucide-react";

import { AttachmentCard, AttachmentTypeFace } from "@/components/AttachmentCard";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { UIMediaAttachment } from "@/lib/types";

/** Generated HTML is untrusted content: render it in its own opaque origin. */
const HTML_FRAME_SANDBOX = "allow-scripts";

interface HtmlAttachmentProps {
  attachment: UIMediaAttachment;
  className?: string;
}

/** Inline preview of an HTML attachment with a full-size view and downloads. */
export function HtmlAttachment({ attachment, className }: HtmlAttachmentProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const name = attachment.name || "document.html";
  const url = attachment.url;

  if (!url) {
    return (
      <span data-file-attachment className={cn("not-prose inline-flex w-24 max-w-full shrink-0 flex-col gap-1 align-top", className)}>
        <AttachmentCard name={name} description={t("message.attachmentUnavailable")}>
          <AttachmentTypeFace name={name} kind="html" />
        </AttachmentCard>
      </span>
    );
  }

  const actionClassName = cn(
    "inline-flex h-7 items-center gap-1 rounded-control px-2 text-xs font-medium",
    "text-muted-foreground transition-colors hover:text-foreground",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  );
  const label = t("message.htmlPreview");

  return (
    <figure
      data-html-attachment
      className={cn(
        "not-prose my-3 w-[min(100%,32rem)] overflow-hidden rounded-control",
        "border border-border/60 bg-background/85",
        className,
      )}
    >
      <div className="relative">
        <iframe
          src={url}
          title={name}
          sandbox={HTML_FRAME_SANDBOX}
          loading="lazy"
          tabIndex={-1}
          aria-hidden
          className="pointer-events-none block h-56 w-full border-0 bg-white"
        />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={label}
          className="absolute inset-0 cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      <figcaption className="flex items-center justify-between gap-2 border-t border-border/60 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="min-w-0 truncate text-left text-xs text-foreground/80 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {name}
        </button>
        <span className="flex shrink-0 items-center gap-0.5">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={actionClassName}
            aria-label={t("message.openHtmlInTab", { name })}
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden strokeWidth={1.75} />
          </a>
          <a
            href={url}
            download={name}
            className={actionClassName}
            aria-label={t("message.downloadAttachment", { name })}
          >
            <Download className="h-3.5 w-3.5" aria-hidden strokeWidth={1.75} />
          </a>
        </span>
      </figcaption>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          aria-describedby={undefined}
          className="grid h-[85dvh] w-[min(94vw,64rem)] max-w-none grid-rows-[auto_minmax(0,1fr)] gap-2 overflow-hidden p-2"
        >
          <DialogTitle className="px-2 text-sm font-medium text-muted-foreground">{name}</DialogTitle>
          <iframe
            src={url}
            title={name}
            sandbox={HTML_FRAME_SANDBOX}
            className="block h-full min-h-0 w-full rounded-control border border-border/60 bg-white"
          />
        </DialogContent>
      </Dialog>
    </figure>
  );
}
