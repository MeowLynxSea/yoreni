import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentFrame } from "@/lib/yoreni-client";
import { notificationAppUrl, showBrowserNotification } from "@/lib/browser-notifications";
import type { SendAttachment } from "@/lib/agent-input";
import { toMediaAttachment } from "@/lib/media";
import { playMessageSound } from "@/lib/notification-sound";
import type { ToolProgressEvent, UIMessage, UIMediaWireAttachment } from "@/lib/types";
import { ApiError, fetchPersonalAgent, type AgentHistoryRun } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

const TERMINAL_RUNS = new Set(["completed", "failed", "cancelled"]);

/** The latest user-facing reason from a batch of tool-start events. */
function latestToolReason(events: ToolProgressEvent[] | undefined): string | null {
  let reason: string | null = null;
  for (const event of events ?? []) {
    if (event.phase !== "start") continue;
    const explicit = typeof event.reason === "string" ? event.reason.trim() : "";
    if (explicit) {
      reason = explicit;
      continue;
    }
    const argumentsValue = event.arguments;
    if (argumentsValue && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)) {
      const nested = (argumentsValue as { reason?: unknown }).reason;
      if (typeof nested === "string" && nested.trim()) reason = nested.trim();
    }
  }
  return reason;
}

function runKey(frame: AgentFrame): string {
  return frame.run_id ?? frame.turn_id ?? "notification";
}

function historyMedia(media: UIMediaWireAttachment[] | undefined) {
  return (media ?? []).map((item) => toMediaAttachment(item));
}

/** Replay durable conversation rows in arrival order.
 *
 * Runs recorded before the message projection have no rows: their answer is
 * still the Run result, so both layouts have to coexist in one timeline.
 */
function historyMessages(runs: AgentHistoryRun[]): UIMessage[] {
  return runs.flatMap((run) => {
    const admittedAt = typeof run.created_at_ms === "number" && run.created_at_ms > 0
      ? run.created_at_ms
      : run.event_seq * 2;
    const messages: UIMessage[] = [];
    if (run.kind === "user") {
      const media = historyMedia(run.media_urls);
      messages.push({
        id: run.event_id,
        turnId: run.id,
        role: "user",
        content: run.content,
        createdAt: admittedAt,
        deliveryStatus: "accepted",
        ...(media.length ? { media } : {}),
      });
    }
    for (const entry of run.projection ? run.entries ?? [] : []) {
      const createdAt = typeof entry.created_at_ms === "number" && entry.created_at_ms > 0
        ? entry.created_at_ms
        : admittedAt + messages.length + 1;
      if (entry.event === "user_message") {
        const id = entry.message_id;
        if (!id || messages.some((message) => message.id === id)) continue;
        const media = historyMedia(entry.media_urls);
        messages.push({
          id,
          turnId: run.id,
          role: "user",
          content: entry.text ?? "",
          createdAt,
          deliveryStatus: "accepted",
          ...(media.length ? { media } : {}),
        });
        continue;
      }
      if (entry.event === "message") {
        if (!entry.id) continue;
        const media = historyMedia(entry.media_urls);
        messages.push({
          id: entry.id,
          turnId: run.id,
          role: "assistant",
          content: entry.text ?? "",
          createdAt,
          source: run.kind === "user" ? undefined : { kind: run.kind, label: run.source },
          turnPhase: "complete",
          delivered: true,
          ...(entry.rich ? { rich: true } : {}),
          ...(entry.reply_to ? { replyTo: entry.reply_to } : {}),
          ...(entry.quote ? { quote: entry.quote } : {}),
          ...(media.length ? { media } : {}),
        });
        continue;
      }
      if (entry.event === "reaction" && entry.target && entry.emoji) {
        const index = messages.findIndex((message) => message.id === entry.target);
        if (index < 0) continue;
        const target = messages[index];
        const reactions = target.reactions ?? [];
        if (!reactions.includes(entry.emoji)) {
          messages[index] = { ...target, reactions: [...reactions, entry.emoji] };
        }
      }
    }
    const content = run.result?.content;
    const answered = messages.some((message) => message.delivered);
    if (!answered && typeof content === "string" && content.length > 0) {
      messages.push({
        id: `${run.id}:answer`,
        turnId: run.id,
        role: "assistant",
        content,
        createdAt: admittedAt + messages.length + 1,
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
  const durable = historyMessages(runs);
  // A streamed answer whose durable copy matches keeps its node, so completing
  // a turn does not replay the entrance animation on unchanged text.
  const survived = new Set<string>();
  const merged = current.filter((message) => {
    if (!message.turnId || !terminal.has(message.turnId)) return true;
    if (message.role !== "assistant") return false;
    const settled = durable.some((entry) => entry.role === "assistant"
      && entry.turnId === message.turnId
      && (entry.id === message.id || entry.content === message.content));
    if (!settled) return false;
    survived.add(message.turnId);
    return true;
  });
  for (const message of durable) {
    // Delivered messages carry globally unique IDs, so they always merge by ID.
    // A legacy answer only has its Run as identity and must not duplicate the
    // live bubble that already survived the comparison above.
    if (!message.delivered && message.role === "assistant" && message.turnId
      && survived.has(message.turnId)) continue;
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
    const id = frame.message_id ?? frame.turn_id;
    if (!id) return messages;
    const media = historyMedia(frame.media_urls);
    const existing = messages.find((message) => message.id === id);
    // The sender's optimistic bubble already shows its own previews; the
    // signed copies replace them once the gateway has settled the upload.
    if (existing) {
      return media.length
        ? messages.map((message) => (message.id === id ? { ...message, media } : message))
        : messages;
    }
    return [...messages, {
      id,
      turnId: run,
      role: "user",
      content: frame.text ?? "",
      createdAt: frame.created_at_ms ?? Date.now(),
      deliveryStatus: "accepted",
      ...(media.length ? { media } : {}),
    }];
  }
  if (frame.event === "reaction") {
    if (!frame.target || !frame.emoji) return messages;
    return messages.map((message) => {
      if (message.id !== frame.target) return message;
      const reactions = message.reactions ?? [];
      return reactions.includes(frame.emoji!)
        ? message
        : { ...message, reactions: [...reactions, frame.emoji!] };
    });
  }
  if (frame.event === "message" && frame.id && frame.kind === undefined) {
    // A delivered message is a durable conversation row that owns its bubble;
    // the same ID may already be present from history replay.
    const media = historyMedia(frame.media_urls);
    // Only an explicit excerpt shows a quote; a bare reply stays a reply.
    const quote = frame.quote;
    const delivered: UIMessage = {
      id: frame.id,
      turnId: run,
      role: "assistant",
      content: frame.text ?? "",
      createdAt: frame.created_at_ms ?? Date.now(),
      isStreaming: false,
      turnPhase: "complete",
      delivered: true,
      ...(frame.rich ? { rich: true } : {}),
      ...(frame.reply_to ? { replyTo: frame.reply_to } : {}),
      ...(quote ? { quote } : {}),
      ...(media.length ? { media } : {}),
    };
    const index = messages.findIndex((message) => message.id === frame.id);
    if (index < 0) return [...messages, delivered];
    const existing = messages[index];
    return messages.map((message, position) => position === index
      ? { ...delivered, reactions: existing.reactions ?? delivered.reactions }
      : message);
  }
  if (frame.event === "turn_end") return messages.map((message) => message.turnId === run && !message.delivered
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
    ...(frame.media_urls ? { media: historyMedia(frame.media_urls) } : {}),
  };
  if (!["message", "delta", "stream_end", "reasoning_delta", "reasoning_end", "file_edit"].includes(frame.event)) return messages;
  return index >= 0 ? messages.map((entry, position) => position === index ? message : entry) : [...messages, message];
}

/** `projectId` selects the transcript; `null` is the main chat. */
export function useAgentRun(projectId: string | null = null) {
  const { client, getToken } = useClient();
  const { t } = useTranslation();
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [activeRuns, setActiveRuns] = useState<Map<string, string>>(new Map());
  const [activityByRun, setActivityByRun] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [projectMissing, setProjectMissing] = useState(false);
  const notifiedMessageIds = useRef(new Set<string>());
  const notifiedAuthRequestIds = useRef(new Set<string>());
  // The oldest event sequence the client holds; older pages load from here.
  const oldestSeq = useRef<number | null>(null);
  const olderExhausted = useRef(false);
  // The transcript this hook reads right now. A request or frame for another
  // scope must not land in it.
  const scope = useRef<string | null>(projectId);
  scope.current = projectId;
  const previousScope = useRef<string | null>(projectId);
  useEffect(() => {
    if (previousScope.current === projectId) return;
    previousScope.current = projectId;
    setMessages([]);
    setActiveRuns(new Map());
    setActivityByRun(new Map());
    setError(null);
    setProjectMissing(false);
    setHistoryLoaded(false);
    oldestSeq.current = null;
    olderExhausted.current = false;
  }, [projectId]);
  const refreshHistory = useCallback(async () => {
    const target = projectId;
    try {
      const info = await fetchPersonalAgent(getToken(), undefined, target);
      if (scope.current !== target) return;
      const runs = info.history ?? [];
      if (runs.length > 0) {
        const oldest = Math.min(...runs.map((run) => run.event_seq));
        oldestSeq.current = oldestSeq.current === null ? oldest : Math.min(oldestSeq.current, oldest);
      }
      setMessages((current) => mergeHistory(current, runs));
      setActiveRuns((current) => {
        const next = new Map(current);
        for (const run of runs) {
          if (run.status === "queued" || run.status === "running") next.set(run.event_id, run.id);
          else if (next.get(run.event_id) === run.id) next.delete(run.event_id);
        }
        return next;
      });
      setActivityByRun((current) => {
        const next = new Map(current);
        for (const run of runs) {
          if (run.status !== "queued" && run.status !== "running") {
            next.delete(run.id);
            continue;
          }
          // A live reason arriving during this request is newer than the checkpoint.
          const restored = run.activity?.at(-1)?.reason;
          if (!next.has(run.id) && restored) next.set(run.id, restored);
        }
        return next;
      });
    } catch (reason) {
      // The saved or linked project can be deleted from another window, and the
      // history request is what tells this reader it is gone.
      if (target && scope.current === target && reason instanceof ApiError && reason.status === 404) {
        setProjectMissing(true);
      }
      throw reason;
    } finally {
      if (scope.current === target) setHistoryLoaded(true);
    }
  }, [getToken, projectId]);
  useEffect(() => client.onEvent((frame) => {
    // An authentication card is global, like the credentials it collects:
    // whichever conversation is open shows it, so it alerts from the same place.
    const request = frame.event === "auth_request" ? frame.auth_request : undefined;
    if (request && request.status === "pending" && !notifiedAuthRequestIds.current.has(request.id)) {
      notifiedAuthRequestIds.current.add(request.id);
      const body = (request.purpose ?? "").trim().slice(0, 300) || t("auth.title");
      void showBrowserNotification(body, { key: request.id, url: notificationAppUrl(projectId) });
    }
    // Alerts are not transcript rows: a reply in another conversation still
    // concerns the user, and the alert opens that conversation.
    if (frame.event === "message" && frame.kind === undefined) {
      // A Run whose agent sent nothing still delivers its own text, and that
      // frame has no row ID; key it by the same transcript node it projects to.
      const key = frame.id ?? `${runKey(frame)}:answer`;
      const visible = Boolean(frame.text?.trim() || frame.media_urls?.length);
      if (visible && !notifiedMessageIds.current.has(key)) {
        notifiedMessageIds.current.add(key);
        playMessageSound();
        const body = (frame.text ?? "").trim().slice(0, 300) || t("settings.notifications.newMessage");
        void showBrowserNotification(body, { key, url: notificationAppUrl(frame.project_id) });
      }
    }
    // Every project Run stamps its frames; the main chat leaves the field out.
    if ((frame.project_id ?? null) !== scope.current) return;
    setMessages((current) => projectAgentFrame(current, frame));
    const run = runKey(frame);
    if (frame.event === "message") {
      const reason = latestToolReason(frame.tool_events);
      if (reason) setActivityByRun((current) => new Map(current).set(run, reason));
    }
    if (frame.event === "delta") {
      // Answer text means the described tool step is over; keep only the dots.
      setActivityByRun((current) => {
        if (!current.has(run)) return current;
        const next = new Map(current);
        next.delete(run);
        return next;
      });
    }
    if (frame.event === "accepted" && frame.run_id && frame.event_id) {
      setActiveRuns((current) => new Map(current).set(frame.event_id!, frame.run_id!));
    }
    if (frame.event === "turn_end" || (frame.event === "goal_status" && frame.status === "idle")) {
      setActiveRuns((current) => new Map([...current].filter(([event, run]) => run !== frame.run_id && event !== frame.turn_id)));
      setActivityByRun((current) => {
        if (!current.has(run)) return current;
        const next = new Map(current);
        next.delete(run);
        return next;
      });
    }
    if (frame.event === "error") setError(frame.message ?? (typeof frame.error === "string" ? frame.error : frame.error?.message) ?? "Request failed");
    if (frame.event === "turn_end") void refreshHistory().catch(() => {});
  }), [client, refreshHistory, t]);
  useEffect(() => {
    const offReady = client.onReady(() => { void refreshHistory().catch(() => {}); });
    void refreshHistory().catch(() => {});
    return offReady;
  }, [client, refreshHistory]);
  // Search reaches the whole transcript, so a hit can sit before the window the
  // client loaded; page backwards until the caller has the run it needs.
  const loadOlder = useCallback(async (): Promise<UIMessage[] | null> => {
    const target = projectId;
    const before = oldestSeq.current;
    if (before === null || olderExhausted.current) return null;
    const info = await fetchPersonalAgent(getToken(), before, target);
    if (scope.current !== target) return null;
    const runs = info.history ?? [];
    if (runs.length === 0) {
      olderExhausted.current = true;
      return [];
    }
    setMessages((current) => mergeHistory(current, runs));
    const nextOldest = Math.min(...runs.map((run) => run.event_seq));
    if (nextOldest >= before) olderExhausted.current = true;
    oldestSeq.current = nextOldest;
    return historyMessages(runs);
  }, [getToken, projectId]);
  const send = useCallback(async (content: string, attachments: SendAttachment[] = []) => {
    const eventId = crypto.randomUUID();
    // The previews the composer already showed belong to this turn, so the
    // waiting bubble renders the same attachments it is about to deliver.
    const media = attachments.map((item) => item.preview);
    setError(null);
    setMessages((current) => [...current, {
      id: eventId, turnId: eventId, role: "user", content, createdAt: Date.now(),
      deliveryStatus: "sending", ...(media.length ? { media } : {}),
    }]);
    try { await client.submit(content, attachments.map((item) => item.media), { eventId, projectId }); }
    catch (reason) {
      setMessages((current) => current.map((message) => message.id === eventId ? { ...message, deliveryStatus: "failed" } : message));
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    }
  }, [client, projectId]);
  const stop = useCallback(async () => {
    await Promise.all([...activeRuns.values()].map((runId) => client.requestMutation("agent.cancel", { run_id: runId })));
  }, [activeRuns, client]);
  // A message awaiting acknowledgment is already an agent run from the reader's
  // point of view; the indicator must not blink between the optimistic send and
  // the run recorded in history.
  const sending = messages.some((message) => message.deliveryStatus === "sending");
  const activity = [...activityByRun.entries()]
    .filter(([runId]) => [...activeRuns.values()].includes(runId))
    .at(-1)?.[1] ?? null;
  return { messages, send, stop, loadOlder, active: activeRuns.size > 0, sending, activity, error, historyLoaded, projectMissing };
}
