import { cn } from "../../lib/cn";

export interface CardProps {
  className?: string;
  children: React.ReactNode;
  as?: "div" | "section" | "article";
}

export function Card({ className, children, as: Tag = "div" }: CardProps) {
  return <Tag className={cn("card p-5", className)}>{children}</Tag>;
}

export function CardHeader({
  title,
  subtitle,
  action,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <h3 className="truncate text-base font-semibold text-content">{title}</h3>
        {subtitle ? (
          <p className="mt-0.5 text-sm text-muted">{subtitle}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({ className, children }: CardProps) {
  return <div className={cn("", className)}>{children}</div>;
}

export function CardFooter({ className, children }: CardProps) {
  return (
    <div className={cn("mt-4 flex items-center justify-end gap-2", className)}>
      {children}
    </div>
  );
}
