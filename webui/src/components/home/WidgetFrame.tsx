import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

import { cn } from "@/lib/utils";

interface WidgetFrameValue {
  collapsible: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

const WidgetFrameContext = createContext<WidgetFrameValue | null>(null);

export function useWidgetFrame(): WidgetFrameValue | null {
  return useContext(WidgetFrameContext);
}

/**
 * Widgets still return `null` when they have nothing to show; the frame owns the
 * shared show/hide motion by tracking whether the widget rendered anything.
 */
function useHasContent(node: RefObject<HTMLDivElement | null>) {
  const [present, setPresent] = useState(false);
  useLayoutEffect(() => {
    const element = node.current;
    if (!element) return;
    const update = () => setPresent(element.childElementCount > 0);
    update();
    const observer = new MutationObserver(update);
    observer.observe(element, { childList: true });
    return () => observer.disconnect();
  }, [node]);
  return present;
}

export function WidgetFrame({
  id,
  collapsible,
  collapsed,
  onToggleCollapsed,
  onPresenceChange,
  children,
}: {
  id: string;
  collapsible: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onPresenceChange: (id: string, present: boolean) => void;
  children: ReactNode;
}) {
  const content = useRef<HTMLDivElement>(null);
  const present = useHasContent(content);

  useLayoutEffect(() => {
    onPresenceChange(id, present);
  }, [id, onPresenceChange, present]);

  return (
    <div
      className={cn(
        "grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none",
        present ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
      )}
    >
      <div className="min-h-0 overflow-hidden">
        <div ref={content} className="pt-3">
          <WidgetFrameContext.Provider value={{ collapsible, collapsed, onToggleCollapsed }}>
            {children}
          </WidgetFrameContext.Provider>
        </div>
      </div>
    </div>
  );
}
