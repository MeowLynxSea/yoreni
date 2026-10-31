/**
 * One emoji artwork for the whole interface.
 *
 * Emoji are drawn with the Twemoji SVGs copied into `public/emoji` at build
 * time, so a message looks the same on macOS, Windows and Linux. The manifest
 * records which codepoints the artwork covers; anything outside it keeps the
 * platform glyph rather than showing a broken image.
 */

const MANIFEST_URL = "/emoji/manifest.json";
const ARTWORK_BASE = "/emoji/";

let covered: Set<string> | null = null;

/** Read the artwork manifest once, before the first message renders. */
export async function loadEmojiArtwork(): Promise<void> {
  if (covered) return;
  try {
    const response = await fetch(MANIFEST_URL, { cache: "force-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const entries: unknown = await response.json();
    covered = new Set(
      Array.isArray(entries)
        ? entries.filter((entry): entry is string => typeof entry === "string")
        : [],
    );
  } catch {
    // Without the manifest the interface keeps native glyphs; nothing breaks.
    covered = new Set();
  }
}

/** Codepoints of one emoji, in the file naming the artwork uses. */
export function hexcodeOf(emoji: string): string {
  return [...emoji]
    .map((character) => character.codePointAt(0)?.toString(16) ?? "")
    .filter(Boolean)
    .join("-");
}

/** The artwork file for an emoji, or null when this build has none. */
export function emojiArtwork(emoji: string): string | null {
  const base = hexcodeOf(emoji);
  const candidates = [base, base.replaceAll("-fe0f", ""), `${base}-fe0f`];
  for (const candidate of candidates) {
    if (covered?.has(candidate)) return `${ARTWORK_BASE}${candidate}.svg`;
  }
  return null;
}

export function emojiArtworkUrl(hexcode: string): string {
  return `${ARTWORK_BASE}${hexcode.toLowerCase()}.svg`;
}

export function hasEmojiArtwork(hexcode: string): boolean {
  return covered?.has(hexcode.toLowerCase()) ?? false;
}

export type EmojiRun =
  | { kind: "text"; text: string }
  | { kind: "emoji"; emoji: string };

// Regional indicators (flags), keycaps, and pictographs with skin tones, ZWJ
// joins and tag sequences. Code spans are the caller's responsibility.
const EMOJI_SEQUENCE =
  /(?:\p{Regional_Indicator}{2}|[#*0-9]\uFE0F?\u20E3|\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic}|[\u{1F3FB}-\u{1F3FF}]|[\u{E0020}-\u{E007F}])*)/gu;

/** Split text into plain runs and emoji runs, preserving order. */
export function splitEmojiRuns(text: string): EmojiRun[] {
  const runs: EmojiRun[] = [];
  let cursor = 0;
  for (const match of text.matchAll(EMOJI_SEQUENCE)) {
    const start = match.index ?? 0;
    if (start > cursor) runs.push({ kind: "text", text: text.slice(cursor, start) });
    runs.push({ kind: "emoji", emoji: match[0] });
    cursor = start + match[0].length;
  }
  if (cursor < text.length) runs.push({ kind: "text", text: text.slice(cursor) });
  return runs;
}
