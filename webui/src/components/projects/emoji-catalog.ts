/**
 * The emoji catalogue offered when naming a project.
 *
 * Records come from emojibase (CLDR names and keywords, translated where the
 * data set supports the interface language) and are drawn with the Twemoji
 * artwork copied into `public/emoji` at build time, so every platform shows the
 * same picture instead of its own font.
 */

interface EmojiRecord {
  label: string;
  hexcode: string;
  emoji: string;
  tags?: string[];
  order?: number;
}

export interface EmojiOption {
  emoji: string;
  hexcode: string;
  label: string;
  search: string;
}

type EmojiModule = { default: EmojiRecord[] };

const SOURCES: Record<string, () => Promise<EmojiModule>> = {
  en: () => import("emojibase-data/en/data.json"),
  zh: () => import("emojibase-data/zh/data.json"),
  "zh-hant": () => import("emojibase-data/zh-hant/data.json"),
};

const FALLBACK_LOCALE = "en";
const cache = new Map<string, EmojiOption[]>();

/** Map an interface locale to the closest data set that ships names. */
export function emojiLocale(language: string): string {
  const normalised = language.toLowerCase();
  if (normalised.startsWith("zh-tw") || normalised.startsWith("zh-hk") || normalised.startsWith("zh-hant")) {
    return "zh-hant";
  }
  if (normalised.startsWith("zh")) return "zh";
  return FALLBACK_LOCALE;
}

const normalise = (value: string) => value.toLocaleLowerCase().replace(/\s+/g, " ").trim();

function toOption(record: EmojiRecord): EmojiOption {
  const tags = record.tags ?? [];
  return {
    emoji: record.emoji,
    hexcode: record.hexcode.toLowerCase(),
    label: record.label,
    search: normalise(`${record.label} ${tags.join(" ")} ${record.emoji}`),
  };
}

/** Load (once) the catalogue for an interface language. */
export async function loadEmojis(language: string): Promise<EmojiOption[]> {
  const locale = emojiLocale(language);
  const cached = cache.get(locale);
  if (cached) return cached;
  const module = await (SOURCES[locale] ?? SOURCES[FALLBACK_LOCALE])();
  const options = module.default
    .filter((record) => record.emoji && record.hexcode)
    .sort((left, right) => (left.order ?? 0) - (right.order ?? 0))
    .map(toOption);
  cache.set(locale, options);
  return options;
}

/** Emojis whose name, keywords or glyph match the query, in catalogue order. */
export function searchEmojis(options: readonly EmojiOption[], query: string): EmojiOption[] {
  const needle = normalise(query);
  if (!needle) return [...options];
  return options.filter((option) => option.search.includes(needle));
}

