import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act, useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageActionLayer } from "@/components/thread/MessageActionLayer";
import type { MessageQuoteSelection } from "@/components/thread/MessageActionLayer";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "clipboard");
});

async function nextFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

function stubCollapsedSelection() {
  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: true,
    rangeCount: 0,
    toString: () => "",
  } as unknown as Selection);
}

function stubSelectionOver(text: string) {
  const node = screen.getByText(text).firstChild!;
  const range = document.createRange();
  range.setStart(node, 0);
  range.setEnd(node, 5);
  vi.spyOn(range, "getBoundingClientRect").mockReturnValue({
    left: 100, right: 140, top: 100, bottom: 120, width: 40, height: 20, x: 100, y: 100,
    toJSON: () => ({}),
  });
  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
    toString: () => "hello",
  } as unknown as Selection);
}

function Harness({ onQuote }: { onQuote: (selection: MessageQuoteSelection) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={containerRef}>
      <MessageActionLayer
        containerRef={containerRef}
        messageText={(messageId) => messageId === "u1" ? "hello there" : null}
        onQuote={onQuote}
      />
      <div data-message-id="u1">
        <p data-message-selectable="true">hello there</p>
      </div>
    </div>
  );
}

function selectText(text: string, start: number, end: number) {
  const node = screen.getByText(text).firstChild!;
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, end);
  vi.spyOn(range, "getBoundingClientRect").mockReturnValue({
    left: 100, right: 240, top: 100, bottom: 120, width: 140, height: 20, x: 100, y: 100,
    toJSON: () => ({}),
  });
  const removeAllRanges = vi.fn();
  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
    toString: () => "hello",
    removeAllRanges,
  } as unknown as Selection);
  document.dispatchEvent(new Event("selectionchange"));
  return { removeAllRanges };
}

describe("message actions", () => {
  it("quotes the selected passage and clears it", async () => {
    const onQuote = vi.fn();
    render(<Harness onQuote={onQuote} />);

    const { removeAllRanges } = selectText("hello there", 0, 5);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Quote" }));

    await waitFor(() => expect(onQuote).toHaveBeenCalledWith({ messageId: "u1", text: "hello" }));
    expect(removeAllRanges).toHaveBeenCalled();
  });

  it("copies the selected passage", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<Harness onQuote={vi.fn()} />);

    selectText("hello there", 0, 5);
    fireEvent.click(await screen.findByRole("menuitem", { name: "Copy" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("hello"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("offers copy and quote for a whole message from its context menu", async () => {
    const onQuote = vi.fn();
    render(<Harness onQuote={onQuote} />);

    fireEvent.contextMenu(screen.getByText("hello there"), { clientX: 120, clientY: 80 });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Quote" }));

    await waitFor(() => expect(onQuote).toHaveBeenCalledWith({ messageId: "u1", text: "hello there" }));
  });

  it("closes when the reader looks away", async () => {
    render(<Harness onQuote={vi.fn()} />);

    fireEvent.contextMenu(screen.getByText("hello there"), { clientX: 120, clientY: 80 });
    expect(await screen.findByRole("menu")).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("stays open when focusing its actions clears the selection", async () => {
    render(<Harness onQuote={vi.fn()} />);

    fireEvent.contextMenu(screen.getByText("hello there"), { clientX: 120, clientY: 80 });
    expect(await screen.findByRole("menu")).toBeInTheDocument();

    // Focusing the first action collapses whatever the browser still selected.
    stubCollapsedSelection();
    document.dispatchEvent(new Event("selectionchange"));
    await nextFrame();
    await nextFrame();

    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("does not reopen from the selection report that trails a dismissing click", async () => {
    render(<Harness onQuote={vi.fn()} />);

    fireEvent.contextMenu(screen.getByText("hello there"), { clientX: 120, clientY: 80 });
    expect(await screen.findByRole("menu")).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    stubSelectionOver("hello there");
    document.dispatchEvent(new Event("selectionchange"));
    await nextFrame();

    expect(screen.getByRole("menu")).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });
});
