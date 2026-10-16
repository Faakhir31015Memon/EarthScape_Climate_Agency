import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ErrorState } from "../../components/ui/EmptyState";
import { FormField } from "../../components/ui/FormField";
import { Input } from "../../components/ui/Input";
import { useToast } from "../../components/ui/Toast";
import { useAuth } from "./useAuth";
import { ApiError, isApiError } from "../../lib/api/client";

const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: z.string().min(10, "Use at least 10 characters"),
    confirm_password: z.string().min(1, "Re-enter the new password"),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    message: "Passwords do not match",
    path: ["confirm_password"],
  });

type FormValues = z.infer<typeof schema>;

export interface ChangePasswordDialogProps {
  open: boolean;
  onClose: () => void;
  /** When true the dialog cannot be dismissed (forced rotation on login). */
  forced?: boolean;
}

export function ChangePasswordDialog({ open, onClose, forced = false }: ChangePasswordDialogProps) {
  const { changePassword } = useAuth();
  const toast = useToast();
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const {
    handleSubmit,
    control,
    reset,
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  });

  const submit = handleSubmit(async (values) => {
    setServerError(null);
    try {
      await changePassword(values.current_password, values.new_password);
      toast.success("Password updated");
      reset();
      onClose();
    } catch (err) {
      if (isApiError(err)) setServerError(err);
      else setServerError(new ApiError({ message: "Could not change password" }));
    }
  });

  return (
    <Dialog
      open={open}
      onClose={forced ? () => undefined : onClose}
      title={forced ? "Password change required" : "Change password"}
      description={
        forced
          ? "Your account still uses its initial password. Choose a new one to continue."
          : "Update the password you use to sign in."
      }
      footer={
        <>
          {!forced ? (
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          ) : null}
          <Button type="submit" form="change-password-form">
            Update password
          </Button>
        </>
      }
    >
      <form id="change-password-form" onSubmit={submit} className="space-y-4" noValidate>
        {serverError ? (
          <ErrorState message={serverError.message} details={serverError.details} />
        ) : null}
        <FormField name="current_password" control={control} label="Current password" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input
              id={id}
              type="password"
              autoComplete="current-password"
              value={value}
              onChange={onChange}
              onBlur={onBlur}
              aria-invalid={invalid}
              aria-describedby={describedBy}
              invalid={invalid}
            />
          )}
        </FormField>
        <FormField
          name="new_password"
          control={control}
          label="New password"
          hint="At least 10 characters."
          required
        >
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input
              id={id}
              type="password"
              autoComplete="new-password"
              value={value}
              onChange={onChange}
              onBlur={onBlur}
              aria-invalid={invalid}
              aria-describedby={describedBy}
              invalid={invalid}
            />
          )}
        </FormField>
        <FormField name="confirm_password" control={control} label="Confirm new password" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input
              id={id}
              type="password"
              autoComplete="new-password"
              value={value}
              onChange={onChange}
              onBlur={onBlur}
              aria-invalid={invalid}
              aria-describedby={describedBy}
              invalid={invalid}
            />
          )}
        </FormField>
      </form>
    </Dialog>
  );
}
