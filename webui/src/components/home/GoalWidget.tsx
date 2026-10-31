import { useTranslation } from "react-i18next";

import { EmojiText } from "@/components/EmojiText";
import { useGoalState } from "@/hooks/useGoalState";

import { WidgetCard, WidgetPill } from "./WidgetCard";

export function GoalWidget() {
  const { t } = useTranslation();
  const goal = useGoalState();
  const blocked = goal.status === "blocked";
  const title = t("home.widgets.goal.title");

  if (!goal.active && !blocked) return null;

  return (
    <WidgetCard
      title={title}
      badge={(
        <WidgetPill tone={blocked ? "blocked" : "active"}>
          {blocked ? t("home.widgets.goal.blocked") : t("home.widgets.goal.active")}
        </WidgetPill>
      )}
    >
      <p className="text-[14px] font-medium leading-6 text-foreground/90">
        <EmojiText text={goal.ui_summary || goal.objective || ""} />
      </p>
      {goal.ui_summary && goal.objective ? (
        <p className="mt-1.5 text-[13px] leading-6 text-muted-foreground"><EmojiText text={goal.objective} /></p>
      ) : null}
      {blocked && goal.recap ? (
        <p className="mt-3 text-[13px] leading-6 text-amber-700/90 dark:text-amber-300/90"><EmojiText text={goal.recap} /></p>
      ) : null}
    </WidgetCard>
  );
}
