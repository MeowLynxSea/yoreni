import { startsMessageTimeGroup } from "@/lib/format-time";
import type { UIMessage } from "@/lib/types";

export interface MessageTimeGroup {
  key: string;
  /** Local timestamp of the group's first message, shown above the group. */
  startedAt: number;
  messages: UIMessage[];
}

/**
 * Split a transcript into runs of messages that belong to one moment, so a
 * resumed conversation shows when it picked up again instead of one undated wall.
 */
export function messageTimeGroups(messages: UIMessage[]): MessageTimeGroup[] {
  const groups: MessageTimeGroup[] = [];
  let previous: number | null = null;
  for (const message of messages) {
    const current = groups[groups.length - 1];
    if (!current || startsMessageTimeGroup(previous, message.createdAt)) {
      groups.push({ key: `time-group-${message.id}`, startedAt: message.createdAt, messages: [message] });
    } else {
      current.messages.push(message);
    }
    previous = message.createdAt;
  }
  return groups;
}
