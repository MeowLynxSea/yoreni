/**
 * Draw emoji in Markdown with the same artwork the rest of the interface uses.
 * Code spans keep their literal characters.
 */
import { emojiArtwork, splitEmojiRuns } from "@/lib/emoji-art";

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

const SKIP_TAGS = new Set(["code", "pre", "script", "style"]);

export function rehypeEmojiArtwork() {
  return (tree: HastNode): void => {
    rewrite(tree, false);
  };
}

function rewrite(node: HastNode, skip: boolean): void {
  const children = node.children;
  if (!children) return;
  const next: HastNode[] = [];
  for (const child of children) {
    if (!skip && child.type === "text" && typeof child.value === "string") {
      next.push(...textNodes(child.value));
      continue;
    }
    rewrite(child, skip || (child.type === "element" && SKIP_TAGS.has(child.tagName ?? "")));
    next.push(child);
  }
  node.children = next;
}

function textNodes(value: string): HastNode[] {
  const runs = splitEmojiRuns(value);
  if (runs.length === 1 && runs[0].kind === "text") return [{ type: "text", value }];
  return runs.map((run) => {
    if (run.kind === "text") return { type: "text", value: run.text };
    const src = emojiArtwork(run.emoji);
    if (!src) return { type: "text", value: run.emoji };
    return {
      type: "element",
      tagName: "img",
      properties: {
        src,
        alt: run.emoji,
        className: ["emoji-image"],
        loading: "lazy",
        decoding: "async",
        draggable: "false",
      },
      children: [],
    };
  });
}
