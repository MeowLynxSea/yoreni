import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThreadComposer } from "@/components/thread/ThreadComposer";

const PLACEHOLDER = "Type your message…";

function renderComposer(quotedContext: string | null, onClearQuote = vi.fn()) {
  const view = render(
    <ThreadComposer onSend={vi.fn()} quotedContext={quotedContext} onClearQuote={onClearQuote} />,
  );
  return { ...view, onClearQuote };
}

function quoteStrip() {
  return document.querySelector<HTMLElement>("[data-composer-quote]")!;
}

describe("composer quote strip", () => {
  it("expands after claiming a quote and keeps the text while it collapses", async () => {
    const { rerender, onClearQuote } = renderComposer("earlier answer");

    // The strip paints collapsed first so the next frame has a height to grow from.
    expect(quoteStrip()).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(quoteStrip()).toHaveAttribute("data-state", "open"));
    expect(screen.getByText("earlier answer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove quote" }));
    expect(onClearQuote).toHaveBeenCalled();
    expect(screen.getByPlaceholderText(PLACEHOLDER)).toHaveFocus();

    rerender(<ThreadComposer onSend={vi.fn()} quotedContext={null} onClearQuote={onClearQuote} />);

    // Still mounted, now collapsed, so the composer shrinks instead of jumping.
    expect(quoteStrip()).toHaveAttribute("data-state", "closed");
    expect(screen.getByText("earlier answer")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("earlier answer")).not.toBeInTheDocument());
  });
});
