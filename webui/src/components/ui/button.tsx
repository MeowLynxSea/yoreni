import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-full text-control-label font-medium ring-offset-background transition-[background-color,color,box-shadow,transform] duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-[0_1px_2px_rgba(20,28,42,0.12)] hover:bg-primary/88",
        accent: "bg-[#bf5a42] text-white shadow-[0_1px_2px_rgba(20,28,42,0.12)] hover:bg-[#aa4d37] dark:bg-[#d98770] dark:text-[#1b1412] dark:hover:bg-[#e39a85]",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline:
          "border border-border/75 bg-background/55 backdrop-blur-md hover:bg-accent/70 hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "quiet-control hover:text-accent-foreground",
        icon: "icon-action relative hover:text-foreground active:text-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-5 py-2",
        sm: "h-9 px-4",
        control: "control-layout",
        lg: "h-11 px-7",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    const iconOnly = size === "icon";
    const resolvedVariant = iconOnly && variant === "ghost" ? "icon" : variant;
    return (
      <Comp
        className={cn(
          buttonVariants({ variant: resolvedVariant, size }),
          iconOnly && variant === "outline" && "hover:bg-background",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
