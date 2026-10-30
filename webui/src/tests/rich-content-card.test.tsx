import { render, within } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { preloadMarkdownText } from "@/components/MarkdownText";

beforeAll(preloadMarkdownText);

describe("rich content cards", () => {
  it("draws delivered files as a card instead of a speech bubble", () => {
    const { container } = render(<MessageBubble message={{
      id: "card-1",
      role: "assistant",
      content: "",
      createdAt: 1,
      delivered: true,
      rich: true,
      media: [{ kind: "file", name: "report.pdf", url: "/api/media/signed/report" }],
    }} />);

    const card = container.querySelector("[data-rich-card]");

    expect(card).not.toBeNull();
    expect(container.querySelector(".app-surface-glass")).toBeNull();
    expect(within(card as HTMLElement).getByRole("link", { name: "Download report.pdf" }))
      .toHaveAttribute("href", "/api/media/signed/report");
  });

  it("crops media to the card edge instead of letterboxing it", () => {
    const { container } = render(<MessageBubble message={{
      id: "card-image",
      role: "assistant",
      content: "",
      createdAt: 1,
      delivered: true,
      rich: true,
      media: [{ kind: "image", name: "shot.png", url: "/api/media/signed/shot" }],
    }} />);

    const image = container.querySelector("[data-rich-card] img");

    expect(image).toHaveClass("object-cover");
  });

  it("keeps a caption inside the card", () => {
    const { container } = render(<MessageBubble message={{
      id: "card-2",
      role: "assistant",
      content: "两份报告",
      createdAt: 1,
      delivered: true,
      rich: true,
      media: [{ kind: "file", name: "one.pdf", url: "/api/media/signed/one" }],
    }} />);

    const card = container.querySelector("[data-rich-card]");

    expect(card?.querySelector("figcaption")).toHaveTextContent("两份报告");
  });
});
