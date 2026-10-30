import { describe, expect, it } from "vitest";

import {
  formatQuotedUserMessage,
  parseQuotedUserMessage,
} from "@/lib/user-message-quote";

describe("user message quotes", () => {
  it("round-trips multiline quoted context through the message body", () => {
    const content = formatQuotedUserMessage(
      "What does this mean?",
      "first quoted line\n\nsecond quoted line",
    );

    expect(content).toBe(
      "> [!QUOTE]\n> first quoted line\n>\n> second quoted line\n\nWhat does this mean?",
    );
    expect(parseQuotedUserMessage(content)).toEqual({
      quotedContext: "first quoted line\n\nsecond quoted line",
      quotedMessageId: null,
      content: "What does this mean?",
    });
  });

  it("keeps the row a passage came from so the quote can point back at it", () => {
    const content = formatQuotedUserMessage("And here?", "quoted line", "8f3a-1b2c");

    expect(content).toBe("> [!QUOTE] 8f3a-1b2c\n> quoted line\n\nAnd here?");
    expect(parseQuotedUserMessage(content)).toEqual({
      quotedContext: "quoted line",
      quotedMessageId: "8f3a-1b2c",
      content: "And here?",
    });
  });

  it("leaves ordinary messages and manual blockquotes unchanged", () => {
    const manualBlockquote = "> manually typed quote\n\nordinary message";

    expect(parseQuotedUserMessage(manualBlockquote)).toEqual({
      quotedContext: null,
      quotedMessageId: null,
      content: manualBlockquote,
    });
  });

  it("includes quoted context for slash-prefixed text", () => {
    expect(formatQuotedUserMessage("/model", "selected answer excerpt")).toBe(
      "> [!QUOTE]\n> selected answer excerpt\n\n/model",
    );
  });
});
