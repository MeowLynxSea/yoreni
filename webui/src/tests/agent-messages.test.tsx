import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { preloadMarkdownText } from "@/components/MarkdownText";
import { MessageBubble } from "@/components/MessageBubble";
import { useAgentRun } from "@/hooks/useAgentRun";
import { fetchPersonalAgent, type AgentHistoryEntry, type PersonalAgentInfo } from "@/lib/api";
import type { UIMessage } from "@/lib/types";
import { YoreniClient } from "@/lib/yoreni-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", () => ({ fetchPersonalAgent: vi.fn() }));

class TestSocket {
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send() {}
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
}

function setup() {
  const socket = new TestSocket();
  const client = new YoreniClient({
    url: "ws://test",
    reconnect: false,
    socketFactory: () => socket as unknown as WebSocket,
  });
  client.connect();
  socket.open();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ClientProvider client={client} token="test">{children}</ClientProvider>
  );
  return { client, socket, wrapper };
}

function historyWith(entries: AgentHistoryEntry[], overrides: {
  id?: string; eventId?: string; content?: string; result?: { content: string; stop_reason: string } | null;
} = {}): PersonalAgentInfo {
  const { id = "run-1", eventId = "event-1", content = "look at the plan", result = null } = overrides;
  return {
    id: "agent",
    revision: 1,
    pending_runs: [],
    history: [{
      id,
      status: result ? "completed" : "running",
      error: null,
      event_seq: 1,
      event_id: eventId,
      source: "websocket",
      kind: "user",
      content,
      result,
      created_at_ms: 1_700_000_000_000,
      projection: 1,
      entries,
    }],
  };
}

describe("agent messages", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
  });

  afterEach(() => {
    vi.mocked(fetchPersonalAgent).mockReset();
  });

  it("replays each delivered message as its own bubble", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(historyWith([
      { event: "message", id: "m-1", text: "first thought", created_at_ms: 1_700_000_000_100 },
      { event: "message", id: "m-2", text: "second thought", created_at_ms: 1_700_000_000_200 },
    ]));
    const { client, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    const answers = result.current.messages.filter((message) => message.role === "assistant");
    expect(answers.map((message) => message.content)).toEqual(["first thought", "second thought"]);
    expect(answers.every((message) => message.delivered)).toBe(true);

    unmount();
    client.close();
  });

  it("replays an inserted message and the reaction that acknowledged it", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(historyWith([
      { event: "message", id: "m-1", text: "on it", created_at_ms: 1_700_000_000_100 },
      { event: "user_message", message_id: "event-2", text: "and this too", created_at_ms: 1_700_000_000_200 },
      { event: "reaction", id: "r-1", target: "event-2", emoji: "👀", created_at_ms: 1_700_000_000_300 },
      { event: "message", id: "m-2", text: "done", created_at_ms: 1_700_000_000_400 },
    ]));
    const { client, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    expect(result.current.messages.map((message) => message.content)).toEqual([
      "look at the plan", "on it", "and this too", "done",
    ]);
    const inserted = result.current.messages.find((message) => message.id === "event-2");
    expect(inserted?.reactions).toEqual(["👀"]);

    unmount();
    client.close();
  });

  it("falls back to the run result when the turn sent nothing", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(historyWith([], {
      result: { content: "the plan holds", stop_reason: "complete" },
    }));
    const { client, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages.at(-1)?.content).toBe("the plan holds");
    expect(result.current.messages.at(-1)?.delivered).toBeUndefined();

    unmount();
    client.close();
  });

  it("applies live reactions to their target message", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue({ ...historyWith([]), history: [] });
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    let submitted!: Promise<void>;
    act(() => { submitted = result.current.send("hello"); });
    const eventId = result.current.messages.at(-1)!.id;
    await act(async () => {});
    act(() => socket.receive({ event: "accepted", event_id: eventId, run_id: "run-2" }));
    await act(async () => { await submitted; });

    act(() => socket.receive({ event: "reaction", target: eventId, emoji: "🙂" }));
    act(() => socket.receive({ event: "reaction", target: eventId, emoji: "🙂" }));

    const user = result.current.messages.find((message) => message.id === eventId);
    expect(user?.reactions).toEqual(["🙂"]);

    unmount();
    client.close();
  });

  it("adds a live delivered message under its durable id", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue({ ...historyWith([]), history: [] });
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    let submitted!: Promise<void>;
    act(() => { submitted = result.current.send("hello"); });
    const eventId = result.current.messages.at(-1)!.id;
    await act(async () => {});
    act(() => socket.receive({ event: "accepted", event_id: eventId, run_id: "run-3" }));
    await act(async () => { await submitted; });

    act(() => socket.receive({
      event: "message", id: "m-9", text: "here you go", run_id: "run-3",
      reply_to: eventId, quote: "hello", created_at_ms: 1_700_000_000_500,
    }));

    const answer = result.current.messages.find((message) => message.id === "m-9");
    expect(answer?.content).toBe("here you go");
    expect(answer?.delivered).toBe(true);
    expect(answer?.quote).toBe("hello");
    expect(answer?.replyTo).toBe(eventId);

    unmount();
    client.close();
  });

  it("trims long Agent answers behind a full-message reader", async () => {
    const long = "The plan holds together across every section. ".repeat(60);
    const message: UIMessage = {
      id: "m-long", role: "assistant", content: long, createdAt: 0,
      turnId: "run-1", turnPhase: "complete",
    };
    const restoreLayout = stubMessageLayout({ overflowing: true });
    try {
      const { container } = render(<MessageBubble message={message} />);

      expect(container.querySelector("[data-message-trimmed]")).not.toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Read full message" }));

      const dialog = await screen.findByRole("dialog");
      expect(dialog.textContent?.replace(/\s+/g, " ")).toContain(long.trim().slice(-80).replace(/\s+/g, " "));
    } finally {
      restoreLayout();
    }
  });

  it("trims a long user message the same way", async () => {
    const long = "Please read this long note before replying. ".repeat(80);
    const restoreLayout = stubMessageLayout({ overflowing: true });
    try {
      const { container } = render(
        <MessageBubble message={{ id: "u-long", role: "user", content: long, createdAt: 0 }} />,
      );

      expect(container.querySelector("[data-message-trimmed]")).not.toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Read full message" }));

      const dialog = await screen.findByRole("dialog");
      expect(dialog.textContent?.replace(/\s+/g, " ")).toContain(long.trim().slice(-80).replace(/\s+/g, " "));
    } finally {
      restoreLayout();
    }
  });

  it("leaves a short answer unfolded", () => {
    const restoreLayout = stubMessageLayout({ overflowing: false });
    try {
      const { container } = render(
        <MessageBubble message={{ id: "m-short", role: "assistant", content: "On it.", createdAt: 0 }} />,
      );

      expect(container.querySelector("[data-message-trimmed]")).toBeNull();
      expect(screen.queryByRole("button", { name: "Read full message" })).not.toBeInTheDocument();
    } finally {
      restoreLayout();
    }
  });
});

/** happy-dom performs no layout, so tests supply the measured heights. */
function stubMessageLayout({ overflowing }: { overflowing: boolean }) {
  const scrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
  const clientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.dataset.messageContent !== undefined) return overflowing ? 900 : 300;
      return scrollHeight?.get?.call(this) ?? 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.dataset.messageClip !== undefined) return 416;
      return clientHeight?.get?.call(this) ?? 0;
    },
  });
  return () => {
    if (scrollHeight) Object.defineProperty(HTMLElement.prototype, "scrollHeight", scrollHeight);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight;
    if (clientHeight) Object.defineProperty(HTMLElement.prototype, "clientHeight", clientHeight);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  };
}

