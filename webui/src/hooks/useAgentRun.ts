import { useCallback, useEffect, useState } from "react";
import type { AgentFrame } from "@/lib/yoreni-client";
import type { UIMessage } from "@/lib/types";
import { fetchPersonalAgent, type AgentHistoryRun } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import type { DraftAttachment } from "../../../packages/client-events/attachments";

const TERMINAL_RUNS = new Set(["completed", "failed", "cancelled"]);

function historyMessages(runs: AgentHistoryRun[]): UIMessage[] {
  return runs.flatMap((run) => {
    const createdAt = run.event_seq * 2;
    const messages: UIMessage[] = [];
    if (run.kind === "user") {
      messages.push({
        id: run.event_id,
        turnId: run.id,
        role: "user",
        content: run.content,
        createdAt,
        deliveryStatus: "accepted",
      });
    }
    const content = run.result?.content;
    if (typeof content === "string" && content.length > 0) {
      messages.push({
        id: `${run.id}:answer`,
        turnId: run.id,
        role: "assistant",
        content,
        createdAt: createdAt + 1,
        source: run.kind === "user" ? undefined : { kind: run.kind, label: run.source },
        turnPhase: "complete",
      });
    }
    return messages;
  });
}

/** Merge durable runs without replacing a stream that arrived while the HTTP request was in flight. */
function mergeHistory(current: UIMessage[], runs: AgentHistoryRun[]): UIMessage[] {
  const terminal = new Set(runs.filter((run) => TERMINAL_RUNS.has(run.status)).map((run) => run.id));
  const merged = current.filter((message) => !message.turnId || !terminal.has(message.turnId));
  for (const message of historyMessages(runs)) {
    const index = merged.findIndex((entry) => entry.id === message.id);
    if (index >= 0) {
      const old = merged[index];
      merged[index] = {
        ...old,
        ...message,
        deliveryStatus: old.deliveryStatus === "failed" ? old.deliveryStatus : message.deliveryStatus,
      };
    } else {
      merged.push(message);
    }
  }
  // History uses a monotonic database sequence; live messages use wall time.
  // Sorting here keeps an older run from appearing after a message sent during
  // the asynchronous history request.
  return merged
    .map((message, index) => ({ message, index }))
    .sort((left, right) => left.message.createdAt - right.message.createdAt || left.index - right.index)
    .map(({ message }) => message);
}

/** A live projection of Event/Run output. Refresh recovery reads the Run store. */
export function projectAgentFrame(messages: UIMessage[], frame: AgentFrame): UIMessage[] {
  const run = frame.run_id ?? frame.turn_id ?? "notification";
  if (frame.event === "accepted") return messages.map((message) => message.id === frame.event_id
    ? { ...message, deliveryStatus: "accepted", turnId: frame.run_id ?? message.turnId } : message);
  if (frame.event === "user_message") {
    if (messages.some((message) => message.id === frame.turn_id)) return messages;
    return [...messages, { id: frame.turn_id ?? crypto.randomUUID(), turnId: run, role: "user", content: frame.text ?? "", createdAt: Date.now() }];
  }
  if (frame.event === "turn_end") return messages.map((message) => message.turnId === run
    ? { ...message, isStreaming: false, reasoningStreaming: false, completedAt: Date.now(), latencyMs: frame.latency_ms, usage: frame.usage, roundUsages: frame.round_usages } : message);
  if (["goal_status", "runtime_model_updated", "turn_model_updated", "ready", "goal_state", "pairing_changed", "webui_response"].includes(frame.event)) return messages;
  const reasoning = frame.event.startsWith("reasoning_");
  const trace = frame.event === "file_edit" || (frame.event === "message" && frame.kind !== undefined);
  const id = `${run}:${reasoning ? "reasoning" : trace ? "activity" : frame.stream_id ?? "answer"}`;
  const index = messages.findIndex((message) => message.id === id);
  const old = index >= 0 ? messages[index] : undefined;
  const streaming = frame.event === "delta" || frame.event === "reasoning_delta";
  const message: UIMessage = {
    ...old, id, turnId: run, role: "assistant", kind: trace ? "trace" : "message",
    content: reasoning ? "" : streaming ? (old?.content ?? "") + (frame.text ?? "") : frame.text || old?.content || "",
    createdAt: old?.createdAt ?? Date.now(), isStreaming: streaming,
    ...(reasoning ? { reasoning: (old?.reasoning ?? "") + (frame.text ?? ""), reasoningStreaming: streaming } : {}),
    ...(trace ? { traces: [...old?.traces ?? [], ...frame.text ? [frame.text] : []], toolEvents: frame.tool_events ?? old?.toolEvents, fileEdits: frame.edits ?? old?.fileEdits } : {}),
    ...(frame.media_urls ? { media: frame.media_urls.map((media) => ({ ...media, kind: media.kind === "video" ? "video" : media.kind === "image" ? "image" : "file" })) } : {}),
  };
  if (!["message", "delta", "stream_end", "reasoning_delta", "reasoning_end", "file_edit"].includes(frame.event)) return messages;
  return index >= 0 ? messages.map((entry, position) => position === index ? message : entry) : [...messages, message];
}

export function useAgentRun() {
  const { client, getToken } = useClient();
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [active, setActive] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const refreshHistory = useCallback(async () => {
    try {
      const info = await fetchPersonalAgent(getToken());
      const runs = info.history ?? [];
      setMessages((current) => mergeHistory(current, runs));
      setActive((current) => {
        const next = new Map(current);
        for (const run of runs) {
          if (run.status === "queued" || run.status === "running") next.set(run.event_id, run.id);
          else if (next.get(run.event_id) === run.id) next.delete(run.event_id);
        }
        return next;
      });
    } finally {
      setHistoryLoaded(true);
    }
  }, [getToken]);
  useEffect(() => client.onEvent((frame) => {
    setMessages((current) => projectAgentFrame(current, frame));
    if (frame.event === "accepted" && frame.run_id && frame.event_id) {
      setActive((current) => new Map(current).set(frame.event_id!, frame.run_id!));
    }
    if (frame.event === "turn_end" || (frame.event === "goal_status" && frame.status === "idle")) {
      setActive((current) => new Map([...current].filter(([event, run]) => run !== frame.run_id && event !== frame.turn_id)));
    }
    if (frame.event === "error") setError(frame.message ?? (typeof frame.error === "string" ? frame.error : frame.error?.message) ?? "Request failed");
    if (frame.event === "turn_end") void refreshHistory().catch(() => {});
  }), [client, refreshHistory]);
  useEffect(() => {
    const offReady = client.onReady(() => { void refreshHistory().catch(() => {}); });
    void refreshHistory().catch(() => {});
    return offReady;
  }, [client, refreshHistory]);
  const send = useCallback(async (content: string, media: DraftAttachment[] = []) => {
    const eventId = crypto.randomUUID();
    setError(null);
    setMessages((current) => [...current, { id: eventId, turnId: eventId, role: "user", content, createdAt: Date.now(), deliveryStatus: "sending" }]);
    try { await client.submit(content, media, { eventId }); }
    catch (reason) {
      setMessages((current) => current.map((message) => message.id === eventId ? { ...message, deliveryStatus: "failed" } : message));
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    }
  }, [client]);
  const stop = useCallback(async () => {
    await Promise.all([...active.values()].map((runId) => client.requestMutation("agent.cancel", { run_id: runId })));
  }, [active, client]);
  return { messages, send, stop, active: active.size > 0, error, historyLoaded };
}
