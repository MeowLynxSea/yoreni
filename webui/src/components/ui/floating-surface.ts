export const floatingSurfaceElevationClassName =
  "border border-border/65 bg-popover/95 text-popover-foreground shadow-[0_18px_50px_-20px_rgba(20,28,42,0.28)] backdrop-blur-xl dark:border-white/10 dark:bg-popover/95 dark:shadow-[0_20px_60px_-18px_rgba(0,0,0,0.65)]";

export const floatingSurfaceVisualClassName =
  `rounded-floating p-1.5 ${floatingSurfaceElevationClassName}`;

// A content-owned exit can keep the overlay mounted after its fade finishes.
// Retain the transparent final frame until the modal's presence unmounts it.
export const modalOverlayClassName =
  "fixed inset-0 z-50 bg-[#101722]/20 backdrop-blur-[10px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:fill-mode-forwards data-[state=open]:fade-in-0 dark:bg-black/45";

export const modalSurfaceClassName =
  "border border-border/60 bg-background/95 text-foreground shadow-[0_30px_90px_-32px_rgba(20,28,42,0.42)] backdrop-blur-xl outline-none dark:border-white/10 dark:bg-popover/95 dark:shadow-[0_32px_100px_-30px_rgba(0,0,0,0.75)]";

export const floatingSurfaceClassName =
  `${floatingSurfaceVisualClassName} z-50 overflow-x-hidden overflow-y-auto overscroll-contain scrollbar-thin`;

export const floatingSurfaceMotionClassName =
  "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0";

export const floatingItemClassName =
  "relative flex min-h-8 select-none items-center gap-2 rounded-control px-2.5 py-2 text-[13px] outline-none transition-colors [&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0";

export const floatingItemFocusClassName =
  "focus:bg-foreground/[0.055] focus:text-foreground dark:focus:bg-white/[0.08]";
