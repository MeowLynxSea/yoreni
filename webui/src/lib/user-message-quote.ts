export interface ParsedUserMessageQuote {
  /** The quoted passage, or null when the message carries no quote. */
  quotedContext: string | null;
  /** Conversation row the passage came from, when the client knew it. */
  quotedMessageId: string | null;
  /** The message the user actually typed. */
  content: string;
}

const QUOTED_CONTEXT_MARKER = "> [!QUOTE]";
const QUOTED_MESSAGE_ID = /^[A-Za-z0-9._:-]{1,128}$/;

function normalizeNewlines(value: string): string {
  return value.replace(/\r\n?/g, "\n");
}

/** The model reads the quote inline, so it sees what the user is pointing at. */
export function formatQuotedUserMessage(
  content: string,
  quotedContext?: string | null,
  quotedMessageId?: string | null,
): string {
  const body = content.trim();
  const quote = normalizeNewlines(quotedContext ?? "").trim();
  if (!quote) return body;

  const blockquote = quote
    .split("\n")
    .map((line) => line ? `> ${line}` : ">")
    .join("\n");
  const target = quotedMessageId && QUOTED_MESSAGE_ID.test(quotedMessageId)
    ? ` ${quotedMessageId}`
    : "";
  const quotedMessage = `${QUOTED_CONTEXT_MARKER}${target}\n${blockquote}`;
  return body ? `${quotedMessage}\n\n${body}` : quotedMessage;
}

export function parseQuotedUserMessage(content: string): ParsedUserMessageQuote {
  const normalized = normalizeNewlines(content);
  const markerEnd = normalized.indexOf("\n");
  if (!normalized.startsWith(QUOTED_CONTEXT_MARKER) || markerEnd === -1) {
    return { quotedContext: null, quotedMessageId: null, content };
  }
  const markerId = normalized.slice(QUOTED_CONTEXT_MARKER.length, markerEnd).trim();
  const quotedMessageId = QUOTED_MESSAGE_ID.test(markerId) ? markerId : null;
  if (markerId && quotedMessageId === null) {
    return { quotedContext: null, quotedMessageId: null, content };
  }

  const quoteStart = markerEnd + 1;
  const separatorIndex = normalized.indexOf("\n\n", quoteStart);
  const quoteBlock =
    separatorIndex === -1
      ? normalized.slice(quoteStart)
      : normalized.slice(quoteStart, separatorIndex);
  const quoteLines = quoteBlock.split("\n");
  if (
    quoteLines.length === 0
    || quoteLines.some((line) => line !== ">" && !line.startsWith("> "))
  ) {
    return { quotedContext: null, quotedMessageId: null, content };
  }

  const quotedContext = quoteLines
    .map((line) => line === ">" ? "" : line.slice(2))
    .join("\n")
    .trim();
  if (!quotedContext) {
    return { quotedContext: null, quotedMessageId: null, content };
  }
  return {
    quotedContext,
    quotedMessageId,
    content: separatorIndex === -1 ? "" : normalized.slice(separatorIndex + 2),
  };
}
