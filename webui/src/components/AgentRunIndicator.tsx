import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/** Three pulsing dots plus the agent's own description of its current step. */
export function AgentRunIndicator({
  className,
  activity,
}: {
  className?: string;
  activity?: string | null;
}) {
  const { t } = useTranslation();
  const label = activity?.trim() || t("message.agentWorking");
  return (
    <div
      data-agent-run-indicator
      role="status"
      aria-live="polite"
      aria-label={label}
      className={cn("message-enter flex min-w-0 items-center gap-2 ps-1 text-muted-foreground", className)}
    >
      <span aria-hidden className="flex shrink-0 items-center gap-1.5">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="agent-run-dot"
            style={{ animationDelay: `${index * 160}ms` }}
          />
        ))}
      </span>
      {activity ? (
        <span
          key={activity}
          data-agent-run-activity
          className="agent-activity-enter min-w-0 truncate text-[12.5px] leading-5 text-muted-foreground/80"
        >
          {activity}
        </span>
      ) : null}
    </div>
  );
}
