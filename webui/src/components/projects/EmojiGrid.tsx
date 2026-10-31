import { memo, useCallback, useEffect, useRef, useState } from "react";

import type { EmojiOption } from "@/components/projects/emoji-catalog";
import { ProjectEmoji } from "@/components/projects/ProjectEmoji";
import { cn } from "@/lib/utils";

const COLUMNS = 9;
const CELL = 32;
const GAP = 2;
const STRIDE = CELL + GAP;
const VIEWPORT = 240;
const OVERSCAN_ROWS = 2;

/** Rows to render for one scroll position; exported for testing. */
export function emojiGridWindow(
  scrollTop: number,
  viewportHeight: number,
  rows: number,
): { start: number; end: number } {
  const first = Math.max(0, Math.floor(scrollTop / STRIDE) - OVERSCAN_ROWS);
  const last = Math.min(rows, Math.ceil((scrollTop + viewportHeight) / STRIDE) + OVERSCAN_ROWS);
  return { start: Math.min(first, Math.max(0, rows - 1)), end: Math.max(last, first + 1) };
}

const EmojiCell = memo(function EmojiCell({
  option, selected, onSelect,
}: {
  option: EmojiOption;
  selected: boolean;
  onSelect: (emoji: string) => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      aria-label={option.label}
      title={option.label}
      onClick={() => onSelect(option.emoji)}
      className={cn(
        "grid place-items-center rounded-lg transition-colors hover:bg-foreground/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:hover:bg-white/[0.1]",
        selected && "bg-foreground/[0.08] dark:bg-white/[0.12]",
      )}
      style={{ height: CELL, width: CELL }}
    >
      <ProjectEmoji emoji={option.emoji} className="h-6 w-6" />
    </button>
  );
});

/**
 * Windowed emoji grid. The full catalog stays in memory, but only the rows the
 * reader can see are mounted, so opening the picker never renders thousands of
 * images at once.
 */
export function EmojiGrid({
  options, selected, onSelect, label,
}: {
  options: readonly EmojiOption[];
  selected: string;
  onSelect: (emoji: string) => void;
  label: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const rows = Math.ceil(options.length / COLUMNS);
  const [window, setWindow] = useState(() => emojiGridWindow(0, VIEWPORT, rows));

  const update = useCallback(() => {
    const node = scroller.current;
    if (!node) return;
    const next = emojiGridWindow(node.scrollTop, node.clientHeight || VIEWPORT, rows);
    setWindow((current) => (current.start === next.start && current.end === next.end ? current : next));
  }, [rows]);

  // A new query starts at the top of its own result set.
  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = 0;
    setWindow(emojiGridWindow(0, VIEWPORT, rows));
  }, [rows, options]);

  const visible = options.slice(window.start * COLUMNS, window.end * COLUMNS);
  return (
    <div
      ref={scroller}
      onScroll={update}
      role="listbox"
      aria-label={label}
      className="overflow-y-auto overscroll-contain"
      style={{ height: VIEWPORT }}
    >
      <div style={{ height: rows * STRIDE, position: "relative" }}>
        <div
          className="absolute inset-x-0 grid"
          style={{
            top: window.start * STRIDE,
            gridTemplateColumns: `repeat(${COLUMNS}, ${CELL}px)`,
            gap: GAP,
          }}
        >
          {visible.map((option) => (
            <EmojiCell
              key={option.hexcode}
              option={option}
              selected={option.emoji === selected}
              onSelect={onSelect}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
