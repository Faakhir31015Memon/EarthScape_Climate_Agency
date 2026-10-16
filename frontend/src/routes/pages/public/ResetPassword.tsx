import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { AuthShell } from "../../../components/layout/AuthShell";
import { Button } from "../../../components/ui/Button";
import { EmptyState, ErrorState } from "../../../components/ui/EmptyState";
import { FormField } from "../../../components/ui/FormField";
import { Input } from "../../../components/ui/Input";
import { useAuth } from "../../../features/auth/useAuth";
import { isApiError } from "../../../lib/api/client";

const schema = z
  .object({
    password: z.string().min(10, "Use at least 10 characters"),
    confirm_password: z.string().min(1, "Re-enter your password"),
  })
  .refine((v) => v.password === v.confirm_password, {
    message: "Passwords do not match",
    path: ["confirm_password"],
  });

type FormValues = z.infer<typeof schema>;

export function ResetPasswordPage() {
  const { confirmPasswordReset } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token");
  const [done, setDone] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [details, setDetails] = useState<string[]>([]);

  const { control, handleSubmit } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", confirm_password: "" },
  });

  const submit = handleSubmit(async (values) => {
    if (!token) return;
    setServerError(null);
    setDetails([]);
    try {
      await confirmPasswordReset(token, values.password);
      setDone(true);
    } catch (err) {
      if (isApiError(err)) {
        setServerError(err.message);
        setDetails(err.details);
      } else {
        setServerError("We could not reset the password. The link may have expired.");
      }
    }
  });

  const footer = (
    <Link to="/login" className="font-medium text-primary hover:underline">
      Back to sign in
    </Link>
  );

  if (!token) {
    return (
      <AuthShell title="Reset your password" documentTitle="Reset password" footer={footer}>
        <EmptyState
          title="Reset link is missing"
          reason="This page needs the recovery token from your reset link. Request a new link instead."
          action={
            <Link
              to="/forgot-password"
              className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg"
            >
              Request a new link
            </Link>
          }
        />
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell title="Password updated" documentTitle="Reset password" footer={footer}>
        <p className="text-sm text-muted">
          Your password has been changed. You can now sign in with it.
        </p>
        <Link
          to="/login"
          className="mt-4 inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg"
        >
          Go to sign in
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Choose a new password" documentTitle="Reset password" footer={footer}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {serverError ? <ErrorState message={serverError} details={details} /> : null}
        <FormField name="password" control={control} label="New password" hint="At least 10 characters." required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} type="password" autoComplete="new-password" value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} />
          )}
        </FormField>
        <FormField name="confirm_password" control={control} label="Confirm new password" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} type="password" autoComplete="new-password" value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} />
          )}
        </FormField>
        <Button type="submit" fullWidth>
          Reset password
        </Button>
      </form>
    </AuthShell>
  );
}
