import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { LoadingRing } from "@/components/ui/loading-ring";
import type { ChatSearchHit, ChatSearchLocalResult } from "@/lib/chat-search";
import { formatMessageGroupTime } from "@/lib/format-time";
import { EmojiText } from "@/components/EmojiText";
import { cn } from "@/lib/utils";

type SearchTier = "exact" | "fuzzy" | "semantic";

interface SearchRow {
  hit: ChatSearchHit;
  tier: SearchTier;
}

interface LocalState {
  status: "idle" | "loading" | "done" | "error";
  query: string;
  exact: ChatSearchHit[];
  fuzzy: ChatSearchHit[];
}

interface SemanticState {
  status: "idle" | "loading" | "done" | "error";
  query: string;
  hits: ChatSearchHit[];
}

const LOCAL_DEBOUNCE_MS = 140;
const SEMANTIC_DEBOUNCE_MS = 600;
const MIN_SEMANTIC_QUERY_LENGTH = 2;
const EMPTY_LOCAL: ChatSearchLocalResult = { exact: [], fuzzy: [] };

export interface ChatSearchProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (hit: ChatSearchHit) => void;
  /** Exact and fuzzy matches, ranked by the gateway over the whole history. */
  requestLocal: (query: string) => Promise<ChatSearchLocalResult>;
  /** Model-ranked matches, requested after the local tiers so they stream in. */
  requestSemantic: (query: string) => Promise<ChatSearchHit[]>;
}

function HighlightedSnippet({ text, spans }: { text: string; spans: Array<[number, number]> }) {
  if (spans.length === 0) return <EmojiText text={text} />;
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const [start, end] of spans) {
    if (start < cursor || end <= start) continue;
    if (start > cursor) parts.push(<EmojiText key={`t:${cursor}`} text={text.slice(cursor, start)} />);
    parts.push(
      <mark
        key={`${start}:${end}`}
        className="rounded-[3px] bg-[#bf5a42]/15 px-0.5 text-foreground"
      >
        <EmojiText text={text.slice(start, end)} />
      </mark>,
    );
    cursor = end;
  }
  parts.push(<EmojiText key={`t:${cursor}`} text={text.slice(cursor)} />);
  return <>{parts}</>;
}

function SearchResultRow({
  row,
  selected,
  onMouseEnter,
  onSelect,
}: {
  row: SearchRow;
  selected: boolean;
  onMouseEnter: () => void;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      data-search-result
      data-selected={selected ? "true" : undefined}
      aria-current={selected ? "true" : undefined}
      tabIndex={-1}
      onMouseEnter={onMouseEnter}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-3 px-5 py-3 text-left transition-colors focus:outline-none",
        selected ? "bg-foreground/[0.06] dark:bg-white/[0.075]" : "hover:bg-foreground/[0.035] dark:hover:bg-white/[0.045]",
      )}
    >
      <span className="mt-0.5 shrink-0 rounded-full bg-foreground/[0.06] px-1.5 py-0.5 text-[10px] font-medium leading-4 text-muted-foreground dark:bg-white/[0.08]">
        {t(row.hit.role === "user" ? "search.you" : "search.assistant")}
      </span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 block text-[13px] leading-5 text-foreground/85">
          <HighlightedSnippet text={row.hit.excerpt} spans={row.hit.spans} />
        </span>
      </span>
      <time className="mt-0.5 shrink-0 text-[11px] leading-4 tabular-nums text-muted-foreground/60">
        {formatMessageGroupTime(row.hit.createdAt)}
      </time>
    </button>
  );
}

function SearchSection({
  label,
  rows,
  offset,
  selected,
  onHover,
  onSelect,
}: {
  label: string;
  rows: SearchRow[];
  offset: number;
  selected: number;
  onHover: (index: number) => void;
  onSelect: (row: SearchRow) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <section aria-label={label} className="border-t border-foreground/[0.055] first:border-t-0 dark:border-white/[0.06]">
      <h2 className="px-5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">
        {label}
      </h2>
      <div className="divide-y divide-foreground/[0.05] dark:divide-white/[0.05]">
        {rows.map((row, index) => (
          <SearchResultRow
            key={`${row.tier}:${row.hit.id}`}
            row={row}
            selected={selected === offset + index}
            onMouseEnter={() => onHover(offset + index)}
            onSelect={() => onSelect(row)}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * The chat-wide search surface. It is a full-screen blurred layer whose only
 * visible controls are the query field and its results, as a spotlight search
 * for the conversation. Ranking lives in the gateway, so a freshly opened
 * client searches the whole transcript instead of only what it has loaded.
 */
export function ChatSearch({
  open,
  onOpenChange,
  onSelect,
  requestLocal,
  requestSemantic,
}: ChatSearchProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [local, setLocal] = useState<LocalState>({ status: "idle", query: "", exact: [], fuzzy: [] });
  const [semantic, setSemantic] = useState<SemanticState>({ status: "idle", query: "", hits: [] });
  const trimmedQuery = query.trim();
  const canSearchSemantically = trimmedQuery.length >= MIN_SEMANTIC_QUERY_LENGTH;

  useEffect(() => {
    if (open) return;
    setQuery("");
    setSelected(0);
    setLocal({ status: "idle", query: "", exact: [], fuzzy: [] });
    setSemantic({ status: "idle", query: "", hits: [] });
  }, [open]);

  useEffect(() => {
    setSelected(0);
  }, [trimmedQuery]);

  useEffect(() => {
    if (!open || !trimmedQuery) {
      setLocal({ status: "idle", query: "", exact: [], fuzzy: [] });
      return;
    }
    let cancelled = false;
    setLocal({ status: "loading", query: trimmedQuery, exact: [], fuzzy: [] });
    const timer = window.setTimeout(() => {
      void requestLocal(trimmedQuery)
        .then((result) => {
          if (!cancelled) setLocal({ status: "done", query: trimmedQuery, ...result });
        })
        .catch(() => {
          if (!cancelled) setLocal({ status: "error", query: trimmedQuery, exact: [], fuzzy: [] });
        });
    }, LOCAL_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, trimmedQuery, requestLocal]);

  useEffect(() => {
    if (!open || !canSearchSemantically) {
      setSemantic({ status: "idle", query: "", hits: [] });
      return;
    }
    let cancelled = false;
    setSemantic({ status: "loading", query: trimmedQuery, hits: [] });
    const timer = window.setTimeout(() => {
      void requestSemantic(trimmedQuery)
        .then((hits) => {
          if (!cancelled) setSemantic({ status: "done", query: trimmedQuery, hits });
        })
        .catch(() => {
          if (!cancelled) setSemantic({ status: "error", query: trimmedQuery, hits: [] });
        });
    }, SEMANTIC_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, canSearchSemantically, trimmedQuery, requestSemantic]);

  const localReady = local.query === trimmedQuery;
  const semanticReady = semantic.query === trimmedQuery;
  const localHits = useMemo(
    () => localReady ? local : EMPTY_LOCAL,
    [localReady, local],
  );
  const semanticHits = useMemo(() => {
    if (!semanticReady) return [];
    const localIds = new Set([...localHits.exact, ...localHits.fuzzy].map((hit) => hit.id));
    return semantic.hits.filter((hit) => !localIds.has(hit.id));
  }, [semanticReady, semantic.hits, localHits]);

  const rows = useMemo<SearchRow[]>(() => [
    ...localHits.exact.map((hit) => ({ hit, tier: "exact" as const })),
    ...localHits.fuzzy.map((hit) => ({ hit, tier: "fuzzy" as const })),
    ...semanticHits.map((hit) => ({ hit, tier: "semantic" as const })),
  ], [localHits, semanticHits]);

  const localLoading = local.status === "loading" && local.query === trimmedQuery;
  const semanticLoading = semantic.status === "loading" && semantic.query === trimmedQuery;
  const localSettled = localReady && (local.status === "done" || local.status === "error");
  const localFailed = localReady && local.status === "error";
  const empty = trimmedQuery.length > 0 && localSettled && !semanticLoading && rows.length === 0;

  useEffect(() => {
    if (selected < rows.length) return;
    setSelected(Math.max(0, rows.length - 1));
  }, [rows.length, selected]);

  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector<HTMLElement>("[data-search-result][data-selected='true']")
      ?.scrollIntoView({ block: "nearest" });
  }, [selected, open]);

  const choose = useCallback((row: SearchRow | undefined) => {
    if (!row) return;
    onSelect(row.hit);
    onOpenChange(false);
  }, [onOpenChange, onSelect]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (rows.length > 0) setSelected((current) => (current + 1) % rows.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (rows.length > 0) setSelected((current) => (current - 1 + rows.length) % rows.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      choose(rows[selected]);
    }
  };

  const exactOffset = 0;
  const fuzzyOffset = localHits.exact.length;
  const semanticOffset = fuzzyOffset + localHits.fuzzy.length;
  const showPanel = rows.length > 0 || localLoading || semanticLoading;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-white/20 backdrop-blur-2xl backdrop-saturate-150 dark:bg-[#070a0f]/55",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none",
          )}
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            inputRef.current?.focus();
          }}
          onKeyDown={handleKeyDown}
          onClick={(event) => {
            if (event.target === event.currentTarget) onOpenChange(false);
          }}
          className={cn(
            "fixed inset-0 z-50 flex justify-center overflow-y-auto overscroll-contain px-4 pt-[16vh] focus:outline-none sm:pt-[22vh]",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
            "data-[state=open]:slide-in-from-top-3 motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none",
          )}
        >
          <DialogPrimitive.Title className="sr-only">{t("search.title")}</DialogPrimitive.Title>
          <div className="h-fit w-full max-w-[44rem] pb-10">
            <div
              className={cn(
                "flex h-14 items-center gap-3 rounded-full border border-white/70 bg-white/60 px-5",
                "shadow-[0_24px_70px_-30px_rgba(20,28,42,0.38)] backdrop-blur-2xl",
                "dark:border-white/10 dark:bg-white/[0.075] dark:shadow-[0_26px_80px_-28px_rgba(0,0,0,0.75)]",
              )}
            >
              <Search className="h-[18px] w-[18px] shrink-0 text-muted-foreground/80" strokeWidth={1.8} aria-hidden />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("search.placeholder")}
                aria-label={t("search.title")}
                autoComplete="off"
                spellCheck={false}
                className="h-full min-w-0 flex-1 bg-transparent text-[15px] text-foreground placeholder:text-muted-foreground/65 focus:outline-none"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => {
                    setQuery("");
                    inputRef.current?.focus();
                  }}
                  aria-label={t("search.clear")}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted-foreground/70 transition-colors hover:bg-foreground/[0.06] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 dark:hover:bg-white/[0.08]"
                >
                  <X className="h-4 w-4" strokeWidth={1.8} aria-hidden />
                </button>
              ) : null}
            </div>

            <div
              ref={listRef}
              data-chat-search-results
              className={cn(
                "mt-3 max-h-[52vh] overflow-y-auto overscroll-contain rounded-[1.65rem]",
                "border border-white/60 bg-white/55 backdrop-blur-2xl",
                "shadow-[0_26px_80px_-36px_rgba(20,28,42,0.4)]",
                "dark:border-white/10 dark:bg-[#10141a]/75 dark:shadow-[0_30px_90px_-34px_rgba(0,0,0,0.8)]",
                showPanel ? "block" : "hidden",
              )}
            >
              <SearchSection
                label={t("search.exact")}
                rows={rows.slice(exactOffset, fuzzyOffset)}
                offset={exactOffset}
                selected={selected}
                onHover={setSelected}
                onSelect={choose}
              />
              <SearchSection
                label={t("search.fuzzy")}
                rows={rows.slice(fuzzyOffset, semanticOffset)}
                offset={fuzzyOffset}
                selected={selected}
                onHover={setSelected}
                onSelect={choose}
              />
              <SearchSection
                label={t("search.semantic")}
                rows={rows.slice(semanticOffset)}
                offset={semanticOffset}
                selected={selected}
                onHover={setSelected}
                onSelect={choose}
              />
              {localLoading ? (
                <div className="flex items-center gap-2.5 border-t border-foreground/[0.055] px-5 py-3 text-[12.5px] text-muted-foreground first:border-t-0 dark:border-white/[0.06]">
                  <LoadingRing className="h-3.5 w-3.5" />
                  <span>{t("search.searching")}</span>
                </div>
              ) : semanticLoading ? (
                <div className="flex items-center gap-2.5 border-t border-foreground/[0.055] px-5 py-3 text-[12.5px] text-muted-foreground first:border-t-0 dark:border-white/[0.06]">
                  <LoadingRing className="h-3.5 w-3.5" />
                  <span>{t("search.semanticLoading")}</span>
                </div>
              ) : null}
            </div>

            {localFailed ? (
              <div className="mt-6 text-center">
                <p className="text-[13px] text-muted-foreground/80">{t("search.failed")}</p>
              </div>
            ) : empty ? (
              <div className="mt-6 text-center">
                <p className="text-[13px] text-muted-foreground/80">{t("search.empty")}</p>
                {semantic.status === "error" && semantic.query === trimmedQuery ? (
                  <p className="mt-1 text-[11.5px] text-muted-foreground/60">{t("search.semanticFailed")}</p>
                ) : null}
              </div>
            ) : null}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
