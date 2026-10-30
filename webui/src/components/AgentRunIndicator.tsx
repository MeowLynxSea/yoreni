import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/** Three pulsing dots below the last message while the agent is still working. */
export function AgentRunIndicator({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <div
      data-agent-run-indicator
      role="status"
      aria-live="polite"
      aria-label={t("message.agentWorking")}
      className={cn("message-enter flex items-center gap-1.5 ps-1 text-muted-foreground", className)}
    >
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          aria-hidden
          className="agent-run-dot"
          style={{ animationDelay: `${index * 160}ms` }}
        />
      ))}
    </div>
  );
}
