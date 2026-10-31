import { emojiArtwork } from "@/lib/emoji-art";

/**
 * One emoji drawn with the bundled artwork. A glyph the artwork does not cover
 * (a newer Unicode release, or text stored by an older project) keeps the
 * platform character instead of showing an empty box.
 */
export function ProjectEmoji({ emoji, className }: { emoji: string; className?: string }) {
  if (!emoji) return null;
  const artwork = emojiArtwork(emoji);
  if (!artwork) return <span className={className}>{emoji}</span>;
  return (
    <img
      src={artwork}
      alt={emoji}
      draggable={false}
      loading="lazy"
      decoding="async"
      className={className}
    />
  );
}
