import { useEffect, useMemo, useRef, type PointerEvent, type RefObject } from "react";

/** A finger swipe covers more than its own length, like a laptop trackpad. */
const CURSOR_GAIN = 1.6;
/** Movement that turns a tap into a drag and cancels a long press. */
const TAP_SLOP_PX = 6;
const LONG_PRESS_MS = 400;
const TAP_MAX_MS = 500;
/** noVNC accumulates wheel deltas before it sends one wheel step. */
const WHEEL_GAIN = 1.4;

type Point = { x: number; y: number };

type Gesture = {
  startX: number;
  startY: number;
  moved: boolean;
  dragging: boolean;
  longPress: ReturnType<typeof setTimeout> | null;
  twoFinger: boolean;
  twoFingerMoved: boolean;
  twoFingerAt: number;
  lastMid: Point | null;
};

function createGesture(): Gesture {
  return {
    startX: 0,
    startY: 0,
    moved: false,
    dragging: false,
    longPress: null,
    twoFinger: false,
    twoFingerMoved: false,
    twoFingerAt: 0,
    lastMid: null,
  };
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function clearLongPress(gesture: Gesture): void {
  if (gesture.longPress !== null) {
    clearTimeout(gesture.longPress);
    gesture.longPress = null;
  }
}

/**
 * Relative mouse input over the remote screen.
 *
 * The canvas already consumes absolute touch gestures, so this surface sits
 * above it and paints noVNC's own mouse events at a tracked cursor. Taps click,
 * two-finger taps right-click, two-finger swipes scroll, and a long press then
 * drag keeps the left button held.
 */
export function useTrackpad(screenRef: RefObject<HTMLDivElement>) {
  const cursor = useRef<Point>({ x: 0.5, y: 0.5 });
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture>(createGesture());

  const handlers = useMemo(() => {
    const canvas = (): HTMLCanvasElement | null =>
      screenRef.current?.querySelector("canvas") ?? null;

    const atCursor = () => {
      const target = canvas();
      if (!target) return null;
      const rect = target.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {
        target,
        clientX: rect.left + cursor.current.x * rect.width,
        clientY: rect.top + cursor.current.y * rect.height,
      };
    };

    const send = (type: "mousedown" | "mouseup" | "mousemove", buttons: number) => {
      const point = atCursor();
      if (!point) return;
      point.target.dispatchEvent(new MouseEvent(type, {
        clientX: point.clientX,
        clientY: point.clientY,
        buttons,
        bubbles: true,
        cancelable: true,
      }));
    };

    const scroll = (deltaX: number, deltaY: number) => {
      const point = atCursor();
      if (!point) return;
      point.target.dispatchEvent(new WheelEvent("wheel", {
        clientX: point.clientX,
        clientY: point.clientY,
        deltaX: deltaX * WHEEL_GAIN,
        deltaY: deltaY * WHEEL_GAIN,
        deltaMode: 0,
        bubbles: true,
        cancelable: true,
      }));
    };

    const midpoint = (): Point | null => {
      let x = 0;
      let y = 0;
      for (const point of pointers.current.values()) {
        x += point.x;
        y += point.y;
      }
      const count = pointers.current.size;
      return count ? { x: x / count, y: y / count } : null;
    };

    const reset = () => {
      clearLongPress(gesture.current);
      gesture.current = createGesture();
    };

    const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
      event.currentTarget.setPointerCapture(event.pointerId);
      pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });

      if (pointers.current.size === 1) {
        reset();
        const state = gesture.current;
        state.startX = event.clientX;
        state.startY = event.clientY;
        state.longPress = setTimeout(() => {
          state.longPress = null;
          if (pointers.current.size !== 1 || state.moved) return;
          state.moved = true;
          state.dragging = true;
          send("mousedown", 1);
        }, LONG_PRESS_MS);
        return;
      }

      if (pointers.current.size === 2) {
        const state = gesture.current;
        clearLongPress(state);
        if (state.dragging) {
          send("mouseup", 0);
          state.dragging = false;
        }
        state.twoFinger = true;
        state.twoFingerMoved = false;
        state.twoFingerAt = Date.now();
        state.lastMid = midpoint();
        state.moved = true;
      }
    };

    const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
      const previous = pointers.current.get(event.pointerId);
      if (!previous) return;
      pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const state = gesture.current;

      if (state.twoFinger) {
        if (pointers.current.size < 2) return;
        const mid = midpoint();
        if (mid && state.lastMid) {
          const deltaX = mid.x - state.lastMid.x;
          const deltaY = mid.y - state.lastMid.y;
          if (Math.abs(deltaX) > 0.5 || Math.abs(deltaY) > 0.5) {
            scroll(deltaX, -deltaY);
          }
          if (Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2) state.twoFingerMoved = true;
        }
        state.lastMid = mid;
        return;
      }

      const deltaX = event.clientX - previous.x;
      const deltaY = event.clientY - previous.y;
      if (!deltaX && !deltaY) return;

      if (!state.moved
        && Math.hypot(event.clientX - state.startX, event.clientY - state.startY) > TAP_SLOP_PX) {
        state.moved = true;
        clearLongPress(state);
      }

      const rect = event.currentTarget.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      cursor.current = {
        x: clamp(cursor.current.x + (deltaX / rect.width) * CURSOR_GAIN),
        y: clamp(cursor.current.y + (deltaY / rect.height) * CURSOR_GAIN),
      };
      send("mousemove", state.dragging ? 1 : 0);
    };

    const finish = (event: PointerEvent<HTMLDivElement>, commit: boolean) => {
      if (!pointers.current.delete(event.pointerId)) return;
      const state = gesture.current;
      clearLongPress(state);
      // A second finger usually leaves first; wait for the whole gesture.
      if (pointers.current.size > 0) return;

      if (state.twoFinger) {
        if (commit && !state.twoFingerMoved && Date.now() - state.twoFingerAt < TAP_MAX_MS) {
          send("mousedown", 4);
          send("mouseup", 0);
        }
      } else if (state.dragging) {
        send("mouseup", 0);
      } else if (commit && !state.moved) {
        send("mousedown", 1);
        send("mouseup", 0);
      }
      reset();
    };

    return {
      onPointerDown,
      onPointerMove,
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => finish(event, true),
      onPointerCancel: (event: PointerEvent<HTMLDivElement>) => finish(event, false),
      // Switching modes mid-drag must not leave the left button held down.
      cancel: () => {
        pointers.current.clear();
        if (gesture.current.dragging) send("mouseup", 0);
        reset();
      },
    };
  }, [screenRef]);

  useEffect(() => () => handlers.cancel(), [handlers]);

  return handlers;
}
