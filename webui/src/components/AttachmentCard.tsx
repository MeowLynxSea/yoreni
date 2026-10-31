import { useState, type ReactNode } from "react";
import { FileText, ImageIcon, Video } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FileReferenceIcon, fileKindForPath } from "@/components/FileReferenceChip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { formatFileSize } from "@/lib/format";
import type { UIMediaKind } from "@/lib/types";
import { cn } from "@/lib/utils";

export const attachmentCardActionClassName = "grid size-full place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring";

/** The card owns only its information surface; callers own removal, download, and status.
 *
 * `tile` is the square fallback for media that cannot render inline; `row` is the
 * file card, which has space for the name and size beside its type.
 */
export function AttachmentCard({ name, description, children, action, invalid = false, layout = "tile" }: {
  name: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
  invalid?: boolean;
  layout?: "tile" | "row";
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [hintOpen, setHintOpen] = useState(false);
  const changeDetailsOpen = (open: boolean) => { setDetailsOpen(open); setHintOpen(false); };
  const row = layout === "row";
  const details = <>
    <span className="block font-medium">{name}</span>
    {description ? <span className="block text-muted-foreground">{description}</span> : null}
  </>;

  return <span className={cn(
    "relative block shrink-0 rounded-control",
    row ? "w-[19rem] max-w-full" : "h-24 w-24 max-w-full",
    invalid ? "border border-destructive/40 bg-destructive/5 text-destructive" : "message-attachment-card",
  )}>
    <TooltipProvider>
      <Tooltip open={hintOpen && !detailsOpen} onOpenChange={setHintOpen}>
        <Popover open={detailsOpen} onOpenChange={changeDetailsOpen}>
          <PopoverTrigger asChild>
            <TooltipTrigger asChild>
              <button type="button" aria-label={name} aria-description={description}
                onKeyDown={(event) => {
                  // The trigger retains focus while details are open, including during the layer handoff.
                  if (event.key === "Escape" && detailsOpen) {
                    event.preventDefault();
                    event.stopPropagation();
                    changeDetailsOpen(false);
                  }
                }}
                className={cn(
                  "overflow-hidden rounded-[inherit] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                  row ? "block w-full text-left" : "absolute inset-0",
                )}>
                {children}
              </button>
            </TooltipTrigger>
          </PopoverTrigger>
          <PopoverContent side="top" aria-label={name}
            onOpenAutoFocus={(event) => event.preventDefault()}
            className="w-max max-w-[min(20rem,calc(100vw-2rem))] break-words px-3 py-1.5 text-caption">
            {details}
          </PopoverContent>
        </Popover>
        <TooltipContent side="top" className="max-w-[min(20rem,calc(100vw-2rem))] break-words">{details}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
    {action ? <span className={cn(
      "absolute grid h-11 w-11 place-items-center",
      row ? "right-0.5 top-1/2 -translate-y-1/2" : "right-0 top-0",
    )}>{action}</span> : null}
  </span>;
}

function attachmentTypeLabel(name: string, kind: UIMediaKind, t: ReturnType<typeof useTranslation>["t"]): string {
  return name.match(/\.(tar\.(?:gz|bz2|xz)|[a-z0-9]{1,12})$/i)?.[1].toUpperCase()
    || t(kind === "image" ? "message.imageAttachment" : kind === "video" ? "message.videoAttachment" : "message.fileAttachment");
}

export function AttachmentTypeFace({ name, kind = "file" }: { name: string; kind?: UIMediaKind }) {
  const { t } = useTranslation();
  const type = attachmentTypeLabel(name, kind, t);
  const Icon = kind === "image" ? ImageIcon : kind === "video" ? Video : FileText;
  return <span className="flex h-full w-full flex-col items-center justify-center gap-2" aria-hidden>
    <Icon className="h-7 w-7 text-muted-foreground" strokeWidth={1.25} />
    <span className="max-w-full truncate px-2 text-[10px] font-semibold tracking-wide text-muted-foreground">{type}</span>
  </span>;
}

/** A delivered file: its family icon, the name the user can read, and the type and size. */
export function AttachmentFileFace({ name, kind = "file", size }: { name: string; kind?: UIMediaKind; size?: number }) {
  const { t } = useTranslation();
  const type = attachmentTypeLabel(name, kind, t);
  const sizeLabel = typeof size === "number" ? formatFileSize(size) : "";
  return <span className="flex min-w-0 items-center gap-3 py-3 pl-3.5 pr-12">
    <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-[0.7rem] bg-foreground/[0.06] text-muted-foreground">
      <FileReferenceIcon kind={fileKindForPath(name)} className="text-[19px]" />
    </span>
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[13px] font-medium leading-5 text-foreground">{name}</span>
      <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] leading-4 text-muted-foreground">
        <span className="shrink-0 font-semibold tracking-wide">{type}</span>
        {sizeLabel ? <>
          <span aria-hidden className="text-muted-foreground/50">·</span>
          <span className="shrink-0 tabular-nums">{sizeLabel}</span>
        </> : null}
      </span>
    </span>
  </span>;
}
