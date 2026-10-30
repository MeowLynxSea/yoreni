import { uploadAttachments, uploadCapability, type DraftAttachment, type UploadCapability } from "../../../packages/client-events/attachments";
import { createHostWebSocket } from "./runtime";
import type { ConnectionStatus, WorkspaceScopePayload } from "./types";

export interface AgentFrame {
  event: string;
  event_id?: string;
  run_id?: string;
  turn_id?: string;
  text?: string;
  stream_id?: string;
  status?: string;
  outcome?: string;
  kind?: string;
  model?: string;
  model_preset?: string | null;
  message?: string;
  detail?: string;
  error?: { status?: number; message?: string } | string;
  media_urls?: { url: string; name: string; kind?: string }[];
  /** Durable conversation row ID of a delivered Agent message. */
  id?: string;
  /** Conversation row a delivered Agent message answers. */
  reply_to?: string;
  quote?: string;
  /** Emoji reaction target and value. */
  target?: string;
  emoji?: string;
  /** ID of the user message a `user_message` frame records. */
  message_id?: string;
  tool_events?: import("./types").ToolProgressEvent[];
  edits?: import("./types").UIFileEdit[];
  latency_ms?: number;
  usage?: import("./types").UIMessage["usage"];
  round_usages?: import("./types").RoundUsage[];
  request_id?: string;
  ok?: boolean;
  result?: unknown;
  upload?: unknown;
  response_sources?: import("./types").ResponseSource[];
  render_as?: "text";
  created_at_ms?: number;
  goal_state?: import("./types").GoalStateWsPayload;
}
export interface YoreniClientOptions {
  url: string;
  reconnect?: boolean;
  maxFrameBytes?: number;
  onReauth?: () => Promise<string | null>;
  socketFactory?: (url: string) => WebSocket;
  maxBackoffMs?: number;
}
interface Pending {
  frame: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

/** A transport for one Agent. Durable execution and Event deduplication belong to the host. */
export class YoreniClient {
  private socket: WebSocket | null = null;
  private stopped = false;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private upload: UploadCapability | null = null;
  private frames = new Set<(frame: AgentFrame) => void>();
  private statuses = new Set<(status: ConnectionStatus) => void>();
  private ready = new Set<() => void>();
  private pending = new Map<string, Pending>();
  private currentStatus: ConnectionStatus = "closed";

  constructor(private options: YoreniClientOptions) {}
  get status(): ConnectionStatus { return this.currentStatus; }
  onEvent(callback: (frame: AgentFrame) => void) { this.frames.add(callback); return () => { this.frames.delete(callback); }; }
  onReady(callback: () => void) { this.ready.add(callback); return () => { this.ready.delete(callback); }; }
  onStatus(callback: (status: ConnectionStatus) => void) { this.statuses.add(callback); callback(this.status); return () => { this.statuses.delete(callback); }; }
  onPairingChange(callback: () => void) { return this.onEvent((frame) => { if (frame.event === "pairing_changed") callback(); }); }
  onRuntimeModelUpdate(callback: (model: string | null, preset?: string | null) => void) {
    return this.onEvent((frame) => { if (frame.event === "runtime_model_updated") callback(frame.model ?? null, frame.model_preset); });
  }
  updateUrl(url: string, socketFactory?: (url: string) => WebSocket) { this.options = { ...this.options, url, ...(socketFactory ? { socketFactory } : {}) }; }
  updateMaxFrameBytes(maxFrameBytes?: number) { this.options.maxFrameBytes = maxFrameBytes; }
  private statusChanged(status: ConnectionStatus) { this.currentStatus = status; this.statuses.forEach((callback) => callback(status)); }
  connect() { this.stopped = false; if (!this.socket) this.open(); }
  close() {
    this.stopped = true;
    if (this.retry) clearTimeout(this.retry);
    this.socket?.close(); this.socket = null; this.upload = null;
    this.pending.forEach((entry) => { clearTimeout(entry.timer); entry.reject(new Error("Connection closed")); });
    this.pending.clear(); this.statusChanged("closed");
  }
  private open() {
    if (this.stopped) return;
    this.statusChanged("connecting");
    const socket = this.options.socketFactory?.(this.options.url)
      ?? (this.options.url.startsWith("yoreni-host://") ? createHostWebSocket(this.options.url) : new WebSocket(this.options.url));
    this.socket = socket;
    socket.onmessage = (message) => {
      if (this.socket !== socket || typeof message.data !== "string") return;
      let value: unknown;
      try { value = JSON.parse(message.data); } catch { return; }
      if (!value || typeof value !== "object" || !("event" in value) || typeof value.event !== "string") return;
      const frame = value as AgentFrame;
      if (frame.event === "ready") {
        this.upload = uploadCapability(frame.upload); this.attempt = 0;
        this.statusChanged("open"); this.send({ type: "subscribe" });
        this.pending.forEach((entry) => socket.send(entry.frame));
        this.ready.forEach((callback) => callback());
      }
      if (frame.request_id && ["webui_response", "transcription_result", "transcription_error"].includes(frame.event)) {
        const entry = this.pending.get(frame.request_id);
        if (entry) {
          clearTimeout(entry.timer); this.pending.delete(frame.request_id);
          if (frame.ok === false || frame.event === "transcription_error") {
            const error = typeof frame.error === "object" ? frame.error : { message: frame.error ?? frame.message };
            entry.reject(Object.assign(new Error(error.message ?? "Request failed"), { status: error.status ?? 500 }));
          } else entry.resolve(frame.event === "transcription_result" ? frame.text ?? "" : frame.result);
        }
      }
      if (frame.event_id && (frame.event === "accepted" || frame.event === "error")) {
        const entry = this.pending.get(frame.event_id);
        if (entry) {
          clearTimeout(entry.timer);
          this.pending.delete(frame.event_id);
          if (frame.event === "accepted") entry.resolve(frame.event_id);
          else entry.reject(new Error(frame.detail ?? "Input rejected"));
        }
      }
      this.frames.forEach((callback) => callback(frame));
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null; this.upload = null; this.statusChanged("closed");
      if (this.stopped || this.options.reconnect === false) return;
      this.retry = setTimeout(() => { void (async () => {
        const url = await this.options.onReauth?.();
        if (url) this.options.url = url;
        this.open();
      })(); }, Math.min(500 * 2 ** this.attempt++, this.options.maxBackoffMs ?? 10000));
    };
    socket.onerror = () => socket.close();
  }
  private send(value: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== 1) throw new Error("Connection is not ready");
    const frame = JSON.stringify(value);
    if (this.options.maxFrameBytes && new TextEncoder().encode(frame).length > this.options.maxFrameBytes) throw new Error("Message is too large");
    this.socket.send(frame);
  }
  requestMutation<T>(action: string, payload: Record<string, unknown> = {}, timeoutMs = 20000): Promise<T> {
    return this.request<T>({ type: "webui_request", action, payload }, timeoutMs);
  }
  private request<T>(body: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const requestId = crypto.randomUUID();
    const frame = { ...body, request_id: requestId };
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(requestId); reject(new Error("Request timed out")); }, timeoutMs);
      this.pending.set(requestId, { frame: JSON.stringify(frame), resolve: (value) => resolve(value as T), reject, timer });
      try { this.send(frame); } catch (error) { clearTimeout(timer); this.pending.delete(requestId); reject(error); }
    });
  }
  transcribeAudio(dataUrl: string, options?: { durationMs?: number; timeoutMs?: number }) {
    return this.request<string>({ type: "transcribe_audio", data_url: dataUrl, duration_ms: options?.durationMs }, options?.timeoutMs ?? 120000);
  }
  async submit(content: string, media: DraftAttachment[] = [], options: { eventId?: string; workspaceScope?: WorkspaceScopePayload; quote?: string; cliApps?: unknown[]; mcpPresets?: unknown[] } = {}): Promise<string> {
    const socket = this.socket;
    const capability = this.upload;
    const references = await uploadAttachments(media, capability, typeof window === "undefined" ? this.options.url : window.location.href,
      () => this.socket === socket && socket?.readyState === 1 && this.upload === capability);
    const eventId = options.eventId ?? crypto.randomUUID();
    const body = { type: "message", event_id: eventId, content, media: references,
      workspace_scope: options.workspaceScope, quote: options.quote, cli_apps: options.cliApps, mcp_presets: options.mcpPresets };
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(eventId);
        reject(new Error("Input acknowledgment timed out"));
      }, 120000);
      this.pending.set(eventId, { frame: JSON.stringify(body), resolve: () => resolve(eventId), reject, timer });
      try { this.send(body); }
      catch (error) { clearTimeout(timer); this.pending.delete(eventId); reject(error); }
    });
  }
}
