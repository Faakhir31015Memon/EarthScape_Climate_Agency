import { forwardRef, useId, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const FIELD_BASE =
  "w-full rounded-lg border bg-surface px-3 py-2 text-sm text-content placeholder:text-muted/70 " +
  "transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary " +
  "disabled:cursor-not-allowed disabled:opacity-60";

const stateBorder = (invalid?: boolean) =>
  invalid ? "border-danger" : "border-border";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, invalid, ...rest }, ref) => (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(FIELD_BASE, stateBorder(invalid), className)}
      {...rest}
    />
  ),
);
Input.displayName = "Input";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, invalid, rows = 4, ...rest }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={cn(FIELD_BASE, "min-h-20 resize-y", stateBorder(invalid), className)}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, invalid, children, ...rest }, ref) => (
    <select
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(FIELD_BASE, "appearance-none pr-8", stateBorder(invalid), className)}
      {...rest}
    >
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export interface CheckboxProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

/** Standalone labelled checkbox (use FormField when wiring to RHF). */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, className, id, ...rest }, ref) => {
    const autoId = useId();
    const inputId = id ?? autoId;
    return (
      <label
        htmlFor={inputId}
        className={cn("inline-flex cursor-pointer items-center gap-2 text-sm text-content", className)}
      >
        <input
          ref={ref}
          id={inputId}
          type="checkbox"
          className="h-4 w-4 rounded border-border text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          {...rest}
        />
        <span>{label}</span>
      </label>
    );
  },
);
Checkbox.displayName = "Checkbox";

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  description?: string;
}

export const Switch = forwardRef<HTMLInputElement, SwitchProps>(
  ({ label, description, className, id, checked, ...rest }, ref) => {
    const autoId = useId();
    const inputId = id ?? autoId;
    return (
      <label
        htmlFor={inputId}
        className={cn("flex cursor-pointer items-center justify-between gap-4", className)}
      >
        <span className="min-w-0">
          <span className="block text-sm font-medium text-content">{label}</span>
          {description ? <span className="block text-xs text-muted">{description}</span> : null}
        </span>
        <span className="relative inline-flex shrink-0">
          <input
            ref={ref}
            id={inputId}
            type="checkbox"
            role="switch"
            checked={checked}
            className="peer sr-only"
            {...rest}
          />
          <span
            aria-hidden="true"
            className="h-6 w-11 rounded-full bg-surface-2 ring-1 ring-border transition-colors peer-checked:bg-primary peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5"
          />
        </span>
      </label>
    );
  },
);
Switch.displayName = "Switch";
