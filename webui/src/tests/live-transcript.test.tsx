import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { preloadMarkdownText } from "@/components/MarkdownText";
import { AgentRunIndicator } from "@/components/AgentRunIndicator";
import { MessageBubble } from "@/components/MessageBubble";
import { useAgentRun } from "@/hooks/useAgentRun";
import { fetchPersonalAgent, type PersonalAgentInfo } from "@/lib/api";
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

function agentHistory(
  status: "running" | "completed",
  result: { content: string; stop_reason: string } | null = null,
  { id = "run-1", content = "review the plan", eventId = "event-1", activity }: {
    id?: string; content?: string; eventId?: string; activity?: { name: string; reason: string }[];
  } = {},
): PersonalAgentInfo {
  return {
    id: "agent",
    revision: 1,
    pending_runs: [],
    history: [{
      id,
      status,
      error: null,
      event_seq: 1,
      event_id: eventId,
      source: "websocket",
      kind: "user",
      content,
      result,
      activity,
    }],
  };
}

const USER_MESSAGE: UIMessage = { id: "u1", role: "user", content: "hello", createdAt: 0 };

describe("live transcript", () => {
  beforeEach(async () => {
    await preloadMarkdownText();
  });

  afterEach(() => {
    vi.mocked(fetchPersonalAgent).mockReset();
  });

  it("never renders thinking or tool activity", async () => {
    const { container, rerender } = render(<MessageBubble message={{
      id: "trace-1", role: "tool", kind: "trace",
      content: 'search "weather"', traces: ['search "weather"'], createdAt: 0,
    }} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<MessageBubble message={{
      id: "reasoning-1", role: "assistant", content: "",
      reasoning: "comparing two approaches", reasoningStreaming: true, createdAt: 0,
    }} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<MessageBubble message={{
      id: "answer-1", role: "assistant", content: "The plan holds.",
      reasoning: "comparing two approaches", createdAt: 0,
    }} />);
    expect(await screen.findByText("The plan holds.")).toBeVisible();
    expect(screen.queryByText("comparing two approaches")).not.toBeInTheDocument();
  });

  it("opens user and assistant turns with the same entrance", async () => {
    const { container, rerender } = render(<MessageBubble message={USER_MESSAGE} />);
    expect(container.firstElementChild).toHaveClass("message-enter");

    rerender(<MessageBubble message={{
      id: "a1", role: "assistant", content: "hello back", createdAt: 0,
    }} />);
    expect(await screen.findByText("hello back")).toBeVisible();
    expect(container.firstElementChild).toHaveClass("message-enter");
  });

  it("labels the working indicator for assistive technology", () => {
    render(<AgentRunIndicator />);
    expect(screen.getByRole("status", { name: "Agent is working" })).toBeInTheDocument();
  });

  it("shows the agent's current step beside the working indicator", () => {
    render(<AgentRunIndicator activity="Reading the plan before editing." />);
    const status = screen.getByRole("status", { name: "Reading the plan before editing." });
    expect(status).toBeInTheDocument();
    expect(screen.getByText("Reading the plan before editing.")).toBeVisible();
  });

  it("streams step descriptions and restores the active one from history", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(agentHistory("running", null, {
      activity: [{ name: "read_file", reason: "Reviewing the plan." }],
    }));
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.activity).toBe("Reviewing the plan.");

    act(() => socket.receive({
      event: "message", run_id: "run-1", kind: "tool_hint",
      tool_events: [{
        phase: "start", call_id: "call-1", name: "read_file",
        arguments: { path: "plan.md" }, reason: "Checking the plan.",
      }],
    }));
    expect(result.current.activity).toBe("Checking the plan.");

    act(() => socket.receive({ event: "turn_end", run_id: "run-1" }));
    expect(result.current.activity).toBeNull();

    unmount();
    client.close();
  });

  it("restores the working state from history after a reload", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(agentHistory("running"));
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.active).toBe(true);
    expect(result.current.messages.map((message) => message.content)).toEqual(["review the plan"]);

    vi.mocked(fetchPersonalAgent).mockResolvedValue(agentHistory("completed", {
      content: "the plan holds", stop_reason: "complete",
    }));
    act(() => socket.receive({ event: "turn_end", run_id: "run-1" }));

    await waitFor(() => expect(result.current.active).toBe(false));
    await waitFor(() => expect(result.current.messages.at(-1)?.content).toBe("the plan holds"));

    unmount();
    client.close();
  });

  it("keeps the working state on from the optimistic send through the run", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue({ ...agentHistory("completed"), history: [] });
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.active).toBe(false);

    let submitted!: Promise<void>;
    act(() => { submitted = result.current.send("hello"); });
    const eventId = result.current.messages.at(-1)!.id;
    expect(result.current.sending).toBe(true);

    // The transport registers the acknowledgment after the payload upload, so
    // let that settle before replaying the server's acceptance.
    await act(async () => {});
    act(() => socket.receive({ event: "accepted", event_id: eventId, run_id: "run-2" }));
    await act(async () => { await submitted; });
    expect(result.current.sending).toBe(false);
    expect(result.current.active).toBe(true);

    act(() => socket.receive({ event: "turn_end", run_id: "run-2" }));
    expect(result.current.active).toBe(false);

    unmount();
    client.close();
  });

  it("keeps the composer's attachment previews on the sent user turn", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue({ ...agentHistory("completed"), history: [] });
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    const attachment = {
      media: { path: "/tmp/chart.png", name: "chart.png" },
      preview: { kind: "image" as const, url: "blob:preview", name: "chart.png", size: 12 },
    };

    let submitted!: Promise<void>;
    act(() => { submitted = result.current.send("look", [attachment]); });
    const sent = result.current.messages.at(-1)!;
    expect(sent.deliveryStatus).toBe("sending");
    expect(sent.media).toEqual([attachment.preview]);

    await act(async () => {});
    act(() => socket.receive({ event: "accepted", event_id: sent.id, run_id: "run-4" }));
    // The gateway signs the stored copy once the upload has settled.
    act(() => socket.receive({
      event: "user_message", message_id: sent.id, run_id: "run-4", text: "look", created_at_ms: 1,
      media_urls: [{ url: "/api/media/signed/chart", name: "chart.png" }],
    }));
    await act(async () => { await submitted; });

    expect(result.current.messages.at(-1)?.media).toEqual([
      { kind: "image", url: "/api/media/signed/chart", name: "chart.png" },
    ]);

    unmount();
    client.close();
  });

  it("replays a user turn with the attachments it was sent with", async () => {
    const history = agentHistory("completed");
    history.history![0].media_urls = [{ url: "/api/media/signed/report", name: "report.pdf" }];
    vi.mocked(fetchPersonalAgent).mockResolvedValue(history);
    const { client, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    expect(result.current.messages[0].media).toEqual([
      { kind: "file", url: "/api/media/signed/report", name: "report.pdf" },
    ]);

    unmount();
    client.close();
  });

  it("keeps the streamed answer mounted when the durable copy arrives", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue({ ...agentHistory("completed"), history: [] });
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
      event: "delta", run_id: "run-3", stream_id: "stream:0", text: "the answer",
    }));
    const answerId = result.current.messages.find((message) => message.content === "the answer")!.id;

    vi.mocked(fetchPersonalAgent).mockResolvedValue(agentHistory("completed", {
      content: "the answer", stop_reason: "complete",
    }, { id: "run-3", content: "hello", eventId }));
    act(() => socket.receive({ event: "turn_end", run_id: "run-3" }));

    await waitFor(() => expect(vi.mocked(fetchPersonalAgent)).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(result.current.messages.find((message) => message.content === "the answer")?.id).toBe(answerId);
    expect(result.current.messages.find((message) => message.content === "hello")?.id).toBe(eventId);

    unmount();
    client.close();
  });
});
