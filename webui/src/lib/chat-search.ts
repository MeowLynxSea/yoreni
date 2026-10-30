/** One server-ranked search result; the excerpt is already trimmed around the match. */
export interface ChatSearchHit {
  id: string;
  role: "user" | "assistant";
  excerpt: string;
  spans: Array<[number, number]>;
  createdAt: number;
}

export interface ChatSearchLocalResult {
  exact: ChatSearchHit[];
  fuzzy: ChatSearchHit[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toIndex(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function parseHit(raw: unknown): ChatSearchHit | null {
  if (!isRecord(raw)) return null;
  const id = typeof raw.id === "string" ? raw.id : "";
  if (!id || (raw.role !== "user" && raw.role !== "assistant")) return null;
  const excerpt = typeof raw.excerpt === "string" ? raw.excerpt : "";
  const spans: Array<[number, number]> = [];
  if (Array.isArray(raw.spans)) {
    for (const span of raw.spans) {
      if (!Array.isArray(span) || span.length < 2) continue;
      const start = toIndex(span[0]);
      const end = toIndex(span[1]);
      if (end > start && end <= excerpt.length) spans.push([start, end]);
    }
  }
  return { id, role: raw.role, excerpt, spans, createdAt: toIndex(raw.created_at_ms) };
}

function parseHits(raw: unknown): ChatSearchHit[] {
  if (!Array.isArray(raw)) return [];
  const hits: ChatSearchHit[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const hit = parseHit(item);
    if (!hit || seen.has(hit.id)) continue;
    seen.add(hit.id);
    hits.push(hit);
  }
  return hits;
}

export function parseChatSearchResult(raw: unknown): ChatSearchLocalResult {
  const record = isRecord(raw) ? raw : {};
  return { exact: parseHits(record.exact), fuzzy: parseHits(record.fuzzy) };
}

export function parseChatSemanticResult(raw: unknown): ChatSearchHit[] {
  return parseHits(isRecord(raw) ? raw.hits : undefined);
}
