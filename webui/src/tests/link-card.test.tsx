import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { preloadMarkdownText } from "@/components/MarkdownText";

beforeAll(preloadMarkdownText);

describe("link cards", () => {
  it("draws a delivered link as a card instead of a raw URL", () => {
    const { container } = render(<MessageBubble message={{
      id: "link-1",
      role: "assistant",
      content: "",
      createdAt: 1,
      delivered: true,
      rich: true,
      media: [{
        kind: "link",
        url: "https://example.com/a",
        title: "Example",
        description: "A page",
        site: "example.com",
      }],
    }} />);

    const card = container.querySelector("[data-link-card]");

    expect(card).toHaveAttribute("href", "https://example.com/a");
    expect(card).toHaveAttribute("rel", "noopener noreferrer");
    expect(card).toHaveTextContent("Example");
    expect(card).toHaveTextContent("A page");
    expect(card).toHaveTextContent("example.com");
  });

  it("keeps the link kind even when the URL looks like a file", () => {
    const { container } = render(<MessageBubble message={{
      id: "link-2",
      role: "assistant",
      content: "",
      createdAt: 1,
      delivered: true,
      media: [{ kind: "link", url: "https://example.com/report.pdf", title: "Report" }],
    }} />);

    expect(container.querySelector("[data-link-card]")).not.toBeNull();
    expect(container.querySelector("[data-file-attachment]")).toBeNull();
  });
});
