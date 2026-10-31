import { useCallback, useEffect, useMemo, useState } from "react";
import { SmilePlus } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { EmojiOption } from "@/components/projects/emoji-catalog";
import { loadEmojis, searchEmojis } from "@/components/projects/emoji-catalog";
import { EmojiGrid } from "@/components/projects/EmojiGrid";
import { ProjectEmoji } from "@/components/projects/ProjectEmoji";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** Pick a project's emoji by browsing or by typing its name. */
export function EmojiPicker({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<EmojiOption[] | null>(null);

  // Load while the dialog is on screen so the picker opens with its data ready.
  useEffect(() => {
    let live = true;
    void loadEmojis(i18n.language).then(
      (loaded) => { if (live) setOptions(loaded); },
      () => { if (live) setOptions([]); },
    );
    return () => { live = false; };
  }, [i18n.language]);

  const visible = useMemo(() => searchEmojis(options ?? [], query), [options, query]);
  const select = useCallback((emoji: string) => {
    onChange(emoji);
    setOpen(false);
  }, [onChange]);

  return (
    <Popover open={open} onOpenChange={(next) => { setOpen(next); setQuery(""); }}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("projects.emojiPick")}
          title={t("projects.emojiPick")}
          className={cn(
            "grid h-11 w-11 place-items-center rounded-[0.85rem] border border-white/55 bg-white/45 text-[20px] leading-none transition-colors",
            "hover:bg-white/65 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            "dark:border-white/10 dark:bg-white/[0.06] dark:hover:bg-white/[0.1]",
            !value && "text-muted-foreground",
          )}
        >
          {value ? (
            <ProjectEmoji emoji={value} className="h-6 w-6" />
          ) : (
            <SmilePlus className="h-[18px] w-[18px]" strokeWidth={1.7} aria-hidden />
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-1.5">
        <SearchInput
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label={t("projects.emojiSearch")}
          placeholder={t("projects.emojiSearch")}
          className="mb-1.5 h-9 rounded-full"
          autoFocus
        />
        {options === null ? (
          <p role="status" className="px-2 py-3 text-xs text-muted-foreground">{t("projects.emojiLoading")}</p>
        ) : visible.length ? (
          <EmojiGrid
            options={visible}
            selected={value}
            onSelect={select}
            label={t("projects.emojiPick")}
          />
        ) : (
          <p role="status" className="px-2 py-3 text-xs text-muted-foreground">{t("projects.emojiNoMatches")}</p>
        )}
        {value ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-1 w-full justify-start text-[12.5px] text-muted-foreground"
            onClick={() => select("")}
          >
            {t("projects.emojiClear")}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
