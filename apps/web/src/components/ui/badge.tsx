import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-caption font-medium", {
  variants: {
    tone: {
      neutral: "bg-muted text-muted-foreground border-transparent",
      info: "bg-info/12 text-info border-info/25",
      success: "bg-success/12 text-success border-success/25",
      warning: "bg-warning/15 text-warning border-warning/30",
      danger: "bg-destructive/12 text-destructive border-destructive/25",
      primary: "bg-primary/12 text-primary border-primary/25",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...p} />;
}
