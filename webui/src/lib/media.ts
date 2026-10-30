import type { UIMediaAttachment, UIMediaKind } from "@/lib/types";

const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
  ".ico",
  ".svg",
  ".tif",
  ".tiff",
]);

const VIDEO_EXTENSIONS = new Set([
  ".mp4",
  ".webm",
  ".mov",
  ".m4v",
  ".avi",
  ".mkv",
  ".3gp",
]);

const HTML_EXTENSIONS = new Set([".html", ".htm"]);

function cleanPath(value: string): string {
  return value.split(/[?#]/, 1)[0]?.toLowerCase() ?? "";
}

function extensionOf(value?: string): string {
  if (!value) return "";
  const path = cleanPath(value);
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "";
  return path.slice(dot);
}

function explicitMediaKind(media: { url?: string; name?: string }): UIMediaKind | null {
  const url = media.url ?? "";
  if (url.startsWith("data:image/")) return "image";
  if (url.startsWith("data:video/")) return "video";

  const ext = extensionOf(media.name) || extensionOf(url);
  if (!ext) return null;
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (HTML_EXTENSIONS.has(ext)) return "html";
  return "file";
}

export function inferMediaKind(media: { url?: string; name?: string }): UIMediaKind {
  return explicitMediaKind(media) ?? "file";
}

const MEDIA_KINDS = new Set<string>(["image", "video", "html", "file", "link"]);

function isMediaKind(value?: string): value is UIMediaKind {
  return value !== undefined && MEDIA_KINDS.has(value);
}

export function toMediaAttachment(media: {
  url?: string;
  name?: string;
  kind?: string;
  title?: string;
  description?: string;
  site?: string;
}): UIMediaAttachment {
  const declared = isMediaKind(media.kind) ? media.kind : undefined;
  return {
    // A link is a link even when its URL ends in a file extension.
    kind: declared === "link" ? "link" : explicitMediaKind(media) ?? declared ?? "file",
    url: media.url,
    name: media.name,
    ...(media.title ? { title: media.title } : {}),
    ...(media.description ? { description: media.description } : {}),
    ...(media.site ? { site: media.site } : {}),
  };
}
