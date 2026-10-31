import { Fragment, useMemo } from "react";

import { emojiArtwork, splitEmojiRuns } from "@/lib/emoji-art";

/** Plain text with every emoji drawn by the bundled artwork. */
export function EmojiText({ text }: { text: string }) {
  const runs = useMemo(() => splitEmojiRuns(text), [text]);
  return (
    <>
      {runs.map((run, index) => {
        if (run.kind === "text") return <Fragment key={index}>{run.text}</Fragment>;
        const artwork = emojiArtwork(run.emoji);
        return artwork ? (
          <img
            key={index}
            src={artwork}
            alt={run.emoji}
            draggable={false}
            className="emoji-image"
          />
        ) : (
          <Fragment key={index}>{run.emoji}</Fragment>
        );
      })}
    </>
  );
}
