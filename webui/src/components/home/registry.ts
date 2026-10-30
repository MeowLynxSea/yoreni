import { CalendarClock, Target } from "lucide-react";
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";

import { AutomationsWidget } from "./AutomationsWidget";
import { GoalWidget } from "./GoalWidget";

export interface HomeWidgetDefinition {
  id: string;
  titleKey: string;
  icon: LucideIcon;
  Component: ComponentType;
  defaultVisible: boolean;
}

export const HOME_WIDGETS: HomeWidgetDefinition[] = [
  {
    id: "goal",
    titleKey: "home.widgets.goal.title",
    icon: Target,
    Component: GoalWidget,
    defaultVisible: true,
  },
  {
    id: "automations",
    titleKey: "home.widgets.automations.title",
    icon: CalendarClock,
    Component: AutomationsWidget,
    defaultVisible: true,
  },
];
