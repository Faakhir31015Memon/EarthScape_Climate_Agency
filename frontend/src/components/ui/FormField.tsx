import { useId } from "react";
import {
  useController,
  type Control,
  type FieldValues,
  type Path,
} from "react-hook-form";
import { cn } from "../../lib/cn";

export interface FormFieldRenderProps {
  id: string;
  name: string;
  value: string | number | readonly string[];
  invalid: boolean;
  describedBy?: string;
  onChange: (event: unknown) => void;
  onBlur: () => void;
}

export interface FormFieldProps<T extends FieldValues> {
  name: Path<T>;
  control: Control<T>;
  label: string;
  hint?: string;
  required?: boolean;
  className?: string;
  /** Render the input; spread the provided handlers (no register() needed). */
  children: (props: FormFieldRenderProps) => React.ReactNode;
}

/**
 * React Hook Form + Zod accessibility glue. Generates a stable id, wires the
 * label, and connects aria-describedby to the hint + error. Uses useController,
 * so the consumer must NOT also call register() for the same field.
 */
export function FormField<T extends FieldValues>({
  name,
  control,
  label,
  hint,
  required,
  className,
  children,
}: FormFieldProps<T>) {
  const autoId = useId();
  const { field, fieldState } = useController({ name, control });
  const errorId = fieldState.error ? `${autoId}-error` : undefined;
  const hintId = hint ? `${autoId}-hint` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  const value =
    typeof field.value === "string" ||
    typeof field.value === "number" ||
    Array.isArray(field.value)
      ? (field.value as string | number | readonly string[])
      : field.value == null
        ? ""
        : String(field.value);

  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={autoId} className="block text-sm font-medium text-content">
        {label}
        {required ? <span className="ml-0.5 text-danger">*</span> : null}
      </label>
      {children({
        id: autoId,
        name: field.name,
        value,
        invalid: Boolean(fieldState.error),
        describedBy,
        onChange: field.onChange,
        onBlur: field.onBlur,
      })}
      {hint && !fieldState.error ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {fieldState.error ? (
        <p id={errorId} role="alert" className="text-xs font-medium text-danger">
          {fieldState.error.message}
        </p>
      ) : null}
    </div>
  );
}
