import { CalendarClock, RefreshCw, Target } from "lucide-react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";

import { AutomationsWidget } from "./AutomationsWidget";
import { GoalWidget } from "./GoalWidget";
import { MemoryBackfillWidget } from "./MemoryBackfillWidget";

export interface HomeWidgetDefinition {
  id: string;
  titleKey: string;
  icon: LucideIcon;
  Component: ComponentType;
  defaultVisible: boolean;
  collapsible: boolean;
  /** System widgets appear on their own and cannot be switched off. */
  dismissable?: boolean;
}

export const HOME_WIDGETS: HomeWidgetDefinition[] = [
  {
    id: "memory-backfill",
    titleKey: "home.widgets.memoryBackfill.title",
    icon: RefreshCw,
    Component: MemoryBackfillWidget,
    defaultVisible: true,
    collapsible: false,
    dismissable: false,
  },
  {
    id: "goal",
    titleKey: "home.widgets.goal.title",
    icon: Target,
    Component: GoalWidget,
    defaultVisible: true,
    collapsible: true,
  },
  {
    id: "automations",
    titleKey: "home.widgets.automations.title",
    icon: CalendarClock,
    Component: AutomationsWidget,
    defaultVisible: true,
    collapsible: true,
  },
];
