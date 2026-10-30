import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";

import { floatingSurfaceElevationClassName, floatingItemClassName } from "@/components/ui/floating-surface";
import { useFloatingPortal } from "@/components/ui/floating-portal";
import { cn } from "@/lib/utils";
import { ControlChevron } from "@/components/ui/control-chevron";

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;
export const SelectSeparator = SelectPrimitive.Separator;

export const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger ref={ref} {...props}
    className={cn("control-layout justify-between rounded-control border-input bg-background text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 [&>span:first-child]:truncate", className)}>
    {children}
    <SelectPrimitive.Icon><ControlChevron /></SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = "SelectTrigger";

// Popper positioning keeps the list anchored to the trigger without relying on
// a SelectValue or a selected item; item-aligned drops the content into the
// document flow whenever either is missing.
export function SelectContent({ children, ...props }: Pick<
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>,
  "children" | "onPointerUpCapture" | "onPointerDownOutside" | "onKeyDownCapture" | "onEscapeKeyDown" | "onCloseAutoFocus"
>) {
  const container = useFloatingPortal();
  return (
    <SelectPrimitive.Portal container={container ?? undefined}>
      <SelectPrimitive.Content {...props} position="popper" sideOffset={4} collisionPadding={8}
        className={cn("z-50 max-h-[min(24rem,var(--radix-select-content-available-height))] min-w-[max(10rem,var(--radix-select-trigger-width))] overflow-hidden rounded-control border border-transparent", floatingSurfaceElevationClassName)}>
        <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center"><ChevronUp className="h-3.5 w-3.5" /></SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className="p-1">
          {children}
        </SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center"><ChevronDown className="h-3.5 w-3.5" /></SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({ children, className, ...props }: React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item {...props} className={cn(floatingItemClassName, "h-9 py-0 pl-2 pr-8 data-[state=checked]:bg-muted data-[highlighted]:bg-muted data-[disabled]:opacity-50", className)}>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator className="absolute right-2 flex items-center"><Check className="h-3.5 w-3.5" aria-hidden /></SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
