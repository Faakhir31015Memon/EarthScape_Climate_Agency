import { cn } from "../../lib/cn";

export type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

export interface BadgeProps {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
  pill?: boolean;
}

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-content border-border",
  primary: "bg-primary-soft text-primary border-primary/30",
  success: "bg-success/15 text-success border-success/30",
  warning: "bg-warning/15 text-warning border-warning/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  info: "bg-info/15 text-info border-info/30",
};

export function Badge({ tone = "neutral", pill = true, className, children }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-2 py-0.5 text-xs font-medium",
        pill ? "rounded-full" : "rounded-md",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
