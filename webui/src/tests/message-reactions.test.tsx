import { render } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { preloadMarkdownText } from "@/components/MarkdownText";

beforeAll(preloadMarkdownText);

describe("agent reactions", () => {
  it("sits on the corner of the user's bubble instead of below it", () => {
    const { container } = render(<MessageBubble message={{
      id: "u-1",
      role: "user",
      content: "志愿者活动和体测都完成了捏",
      createdAt: 1,
      reactions: ["🎉"],
    }} />);

    const bubble = container.querySelector("[data-user-text-bubble]");
    const reactions = container.querySelector("[data-message-reactions]");

    expect(bubble).not.toBeNull();
    expect(bubble).toContainElement(reactions as HTMLElement);
    expect(reactions).toHaveClass("absolute");
  });

  it("sits on the corner of an answer", () => {
    const { container } = render(<MessageBubble message={{
      id: "a-1",
      role: "assistant",
      content: "收到",
      createdAt: 1,
      reactions: ["👍"],
    }} />);

    const message = container.querySelector("[data-assistant-message]");

    expect(message).toContainElement(
      container.querySelector("[data-message-reactions]") as HTMLElement,
    );
  });
});
