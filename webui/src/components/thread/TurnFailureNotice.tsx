import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Public failure categories from the Run result use wire names; the copy uses
 * stable translation keys. */
const FAILURE_KEYS: Record<string, string> = {
  server: "server",
  rate_limit: "rateLimit",
  timeout: "timeout",
  connection: "connection",
  authentication: "authentication",
  billing: "billing",
  configuration: "configuration",
};

interface TurnFailureNoticeProps {
  failureKind?: string;
}

/** A turn that failed is an outcome, not an answer: no bubble, no reply actions. */
export function TurnFailureNotice({ failureKind }: TurnFailureNoticeProps) {
  const { t } = useTranslation();
  const key = FAILURE_KEYS[failureKind ?? ""] ?? "unknown";

  return (
    <div
      role="status"
      data-turn-failure={failureKind ?? "unknown"}
      className="mx-auto flex w-full max-w-[49.5rem] items-center justify-center gap-2 py-2 text-xs text-destructive/80"
    >
      <span aria-hidden className="h-px min-w-4 max-w-16 flex-1 bg-border/60" />
      <CircleAlert className="h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
      <p className="min-w-0 text-center leading-5">{t(`thread.failure.${key}`)}</p>
      <span aria-hidden className="h-px min-w-4 max-w-16 flex-1 bg-border/60" />
    </div>
  );
}
