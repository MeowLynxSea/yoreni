import { useEffect, useState } from "react";

import { fetchGoal } from "@/lib/api";
import type { GoalStateWsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/** Current sustained goal: loaded once, then kept in sync with runtime frames. */
export function useGoalState(): GoalStateWsPayload {
  const { client, getToken } = useClient();
  const [goal, setGoal] = useState<GoalStateWsPayload>({ active: false });

  useEffect(() => {
    let cancelled = false;
    void fetchGoal(getToken())
      .then((next) => { if (!cancelled) setGoal(next); })
      .catch(() => {});
    const unsubscribe = client.onEvent((frame) => {
      if (frame.event === "goal_state" && frame.goal_state) setGoal(frame.goal_state);
    });
    return () => { cancelled = true; unsubscribe(); };
  }, [client, getToken]);

  return goal;
}
