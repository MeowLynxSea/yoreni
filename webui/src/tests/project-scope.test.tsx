import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAgentRun } from "@/hooks/useAgentRun";
import { fetchPersonalAgent, type PersonalAgentInfo } from "@/lib/api";
import { showBrowserNotification } from "@/lib/browser-notifications";
import { YoreniClient } from "@/lib/yoreni-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", () => ({ fetchPersonalAgent: vi.fn() }));
vi.mock("@/lib/browser-notifications", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/browser-notifications")>()),
  showBrowserNotification: vi.fn(),
}));

class TestSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send(frame: string) { this.sent.push(frame); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
  submitted() {
    return this.sent.map((frame) => JSON.parse(frame) as Record<string, unknown>)
      .filter((frame) => frame.type === "message");
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

function emptyHistory(): PersonalAgentInfo {
  return { id: "agent", revision: 1, pending_runs: [], history: [] };
}

function answerHistory(content: string): PersonalAgentInfo {
  return {
    id: "agent",
    revision: 1,
    pending_runs: [],
    history: [{
      id: "run-1", status: "running", error: null, event_seq: 1, event_id: "event-1",
      source: "websocket", kind: "user", content, result: null, created_at_ms: 1_700_000_000_000,
    }],
  };
}

describe("project transcripts", () => {
  afterEach(() => {
    vi.mocked(fetchPersonalAgent).mockReset();
    vi.mocked(showBrowserNotification).mockReset();
  });

  it("reads and writes the transcript of the active project", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun("p-1"), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    expect(fetchPersonalAgent).toHaveBeenCalledWith("test", undefined, "p-1");

    let submitted!: Promise<void>;
    act(() => { submitted = result.current.send("hello"); });
    const eventId = result.current.messages.at(-1)!.id;
    await act(async () => {});
    act(() => socket.receive({ event: "accepted", event_id: eventId, run_id: "run-2", project_id: "p-1" }));
    await act(async () => { await submitted; });

    expect(socket.submitted().map((frame) => frame.project_id)).toEqual(["p-1"]);

    unmount();
    client.close();
  });

  it("ignores live frames addressed to another conversation", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun("p-1"), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => socket.receive({ event: "message", id: "m-1", text: "another project", project_id: "p-2" }));
    act(() => socket.receive({ event: "message", id: "m-2", text: "main chat" }));
    expect(result.current.messages).toEqual([]);

    act(() => socket.receive({ event: "message", id: "m-3", text: "this project", project_id: "p-1" }));
    expect(result.current.messages.map((message) => message.content)).toEqual(["this project"]);

    unmount();
    client.close();
  });

  it("drops project frames from the main chat", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun(), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => socket.receive({ event: "message", id: "m-1", text: "in a project", project_id: "p-1" }));
    expect(result.current.messages).toEqual([]);

    act(() => socket.receive({ event: "message", id: "m-2", text: "main chat" }));
    expect(result.current.messages.map((message) => message.content)).toEqual(["main chat"]);

    unmount();
    client.close();
  });

  it("alerts once when the agent asks for credentials", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun("p-1"), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    const authRequest = {
      id: "auth_1", connector_id: "portal", method: "api_key", fields: ["api_key"],
      purpose: "Connect the campus portal to check grades.", status: "pending",
    };
    // Cards are not addressed to a project, so a project transcript must still
    // route the alert to the conversation that renders them.
    act(() => socket.receive({ event: "auth_request", auth_request: authRequest }));
    expect(showBrowserNotification).toHaveBeenCalledWith(
      "Connect the campus portal to check grades.",
      { key: "auth_1", url: expect.stringMatching(/#\/projects\/p-1$/) },
    );

    act(() => socket.receive({ event: "auth_request", auth_request: authRequest }));
    expect(showBrowserNotification).toHaveBeenCalledOnce();

    unmount();
    client.close();
  });

  it("alerts for a reply in another conversation and opens that one", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun("p-1"), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => socket.receive({ event: "message", id: "m-9", text: "other project", project_id: "p-2" }));
    expect(showBrowserNotification).toHaveBeenCalledWith(
      "other project",
      { key: "m-9", url: expect.stringMatching(/#\/projects\/p-2$/) },
    );
    expect(result.current.messages).toEqual([]);

    unmount();
    client.close();
  });

  it("alerts for a reply that has no conversation row", async () => {
    vi.mocked(fetchPersonalAgent).mockResolvedValue(emptyHistory());
    const { client, socket, wrapper } = setup();
    const { result, unmount } = renderHook(() => useAgentRun("p-1"), { wrapper });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    // The frame a Run sends when its agent never called send_message carries
    // no row ID, but the transcript still shows this text.
    act(() => socket.receive({ event: "message", run_id: "run-2", text: "  ", project_id: "p-1" }));
    expect(showBrowserNotification).not.toHaveBeenCalled();

    act(() => socket.receive({ event: "message", run_id: "run-2", text: "All done.", project_id: "p-1" }));
    expect(showBrowserNotification).toHaveBeenCalledWith(
      "All done.",
      { key: "run-2:answer", url: expect.stringMatching(/#\/projects\/p-1$/) },
    );

    act(() => socket.receive({ event: "message", run_id: "run-2", text: "All done.", project_id: "p-1" }));
    expect(showBrowserNotification).toHaveBeenCalledOnce();

    unmount();
    client.close();
  });

  it("starts a new transcript when the project changes", async () => {
    vi.mocked(fetchPersonalAgent)
      .mockResolvedValueOnce(answerHistory("first project"))
      .mockResolvedValueOnce(emptyHistory());
    const { client, wrapper } = setup();
    const { result, rerender, unmount } = renderHook(({ projectId }: { projectId: string }) => useAgentRun(projectId), {
      wrapper,
      initialProps: { projectId: "p-1" },
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(1));

    rerender({ projectId: "p-2" });

    await waitFor(() => expect(result.current.messages).toEqual([]));
    expect(fetchPersonalAgent).toHaveBeenLastCalledWith("test", undefined, "p-2");

    unmount();
    client.close();
  });
});
