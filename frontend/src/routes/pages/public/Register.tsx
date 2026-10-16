import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { AuthShell } from "../../../components/layout/AuthShell";
import { Button } from "../../../components/ui/Button";
import { EmptyState, ErrorState } from "../../../components/ui/EmptyState";
import { FormField } from "../../../components/ui/FormField";
import { Input } from "../../../components/ui/Input";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useAuth } from "../../../features/auth/useAuth";
import { useApi } from "../../../lib/api/query";
import { ApiError, isApiError } from "../../../lib/api/client";
import type { RegistrationStatus } from "../../../lib/api/types";

function makeSchema(min: number) {
  return z
    .object({
      full_name: z.string().min(2, "Enter your full name"),
      email: z.string().email("Enter a valid email address"),
      password: z.string().min(min, `Use at least ${min} characters`),
      confirm_password: z.string().min(1, "Re-enter your password"),
    })
    .refine((v) => v.password === v.confirm_password, {
      message: "Passwords do not match",
      path: ["confirm_password"],
    });
}

type FormValues = z.infer<ReturnType<typeof makeSchema>>;

export function RegisterPage() {
  const { register: createAccount } = useAuth();
  const navigate = useNavigate();
  const status = useApi<RegistrationStatus>("/auth/registration-status", []);
  const min = status.data?.password_min_length ?? 10;
  const schema = makeSchema(min);
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const { control, handleSubmit } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: "", email: "", password: "", confirm_password: "" },
  });

  const submit = handleSubmit(async (values) => {
    setServerError(null);
    try {
      await createAccount({
        full_name: values.full_name,
        email: values.email,
        password: values.password,
      });
      navigate("/app", { replace: true });
    } catch (err) {
      setServerError(
        isApiError(err) ? err : new ApiError({ message: "Registration failed." }),
      );
    }
  });

  const footer = (
    <>
      Already have an account?{" "}
      <Link to="/login" className="font-medium text-primary hover:underline">
        Sign in
      </Link>
    </>
  );

  if (status.loading) {
    return (
      <AuthShell title="Create your account" documentTitle="Register">
        <div className="space-y-3" aria-live="polite">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </AuthShell>
    );
  }

  // Honest disabled state: the API says self-registration is closed.
  if (status.data && !status.data.self_registration) {
    return (
      <AuthShell title="Registration is closed" documentTitle="Register" footer={footer}>
        <EmptyState
          title="Self-service sign-up is disabled"
          reason={`Accounts on this deployment are provisioned by an administrator (default role: ${status.data.default_role.replace("_", " ")}; recovery: ${status.data.recovery_channel.replace(/_/g, " ")}). Sign in with the account you were given.`}
        />
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create your account" documentTitle="Register" footer={footer}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {serverError ? (
          <ErrorState title="Registration failed" message={serverError.message} details={serverError.details} />
        ) : null}
        <FormField name="full_name" control={control} label="Full name" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} autoComplete="name" />
          )}
        </FormField>
        <FormField name="email" control={control} label="Email" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} type="email" inputMode="email" autoComplete="email" value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} />
          )}
        </FormField>
        <FormField name="password" control={control} label="Password" hint={`At least ${min} characters.`} required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} type="password" autoComplete="new-password" value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} />
          )}
        </FormField>
        <FormField name="confirm_password" control={control} label="Confirm password" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input id={id} type="password" autoComplete="new-password" value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} />
          )}
        </FormField>
        <Button type="submit" fullWidth>
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}
