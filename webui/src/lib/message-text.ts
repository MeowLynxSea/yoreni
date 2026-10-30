import { parseQuotedUserMessage } from "@/lib/user-message-quote";
import type { UIMessage } from "@/lib/types";

/** The text a reader expects when they copy a message: their own words, not the quote block. */
export function messageCopyText(message: UIMessage, translate: (key: string) => string): string {
  if (message.renderAs === "text") return message.content;
  if (message.role === "user") return parseQuotedUserMessage(message.content).content;
  if (message.compactReply === "empty") return translate("thread.compaction.empty");
  if (message.compactReply === "failed") return translate("thread.compaction.failed");
  return message.content;
}
