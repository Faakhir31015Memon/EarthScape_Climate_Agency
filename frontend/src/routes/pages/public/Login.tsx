import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { AuthShell } from "../../../components/layout/AuthShell";
import { Button } from "../../../components/ui/Button";
import { ErrorState } from "../../../components/ui/EmptyState";
import { FormField } from "../../../components/ui/FormField";
import { Input } from "../../../components/ui/Input";
import { useAuth } from "../../../features/auth/useAuth";
import { ApiError, isApiError } from "../../../lib/api/client";

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});

type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [serverError, setServerError] = useState<ApiError | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/app";

  const { control, handleSubmit } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  const submit = handleSubmit(async (values) => {
    setServerError(null);
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch (err) {
      setServerError(
        isApiError(err) ? err : new ApiError({ message: "Sign-in failed. Try again." }),
      );
    }
  });

  return (
    <AuthShell
      title="Sign in"
      subtitle="Access your EarthScape climate workspace."
      documentTitle="Sign in"
      footer={
        <>
          <Link to="/forgot-password" className="font-medium text-primary hover:underline">
            Forgot password?
          </Link>
          <span className="mx-2 text-border">·</span>
          <Link to="/register" className="font-medium text-primary hover:underline">
            Register
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        {serverError ? (
          <ErrorState
            title="Could not sign you in"
            message={serverError.message}
            details={serverError.details}
          />
        ) : null}
        <FormField name="email" control={control} label="Email" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input
              id={id}
              type="email"
              autoComplete="email"
              inputMode="email"
              value={value}
              onChange={onChange}
              onBlur={onBlur}
              aria-invalid={invalid}
              aria-describedby={describedBy}
              invalid={invalid}
            />
          )}
        </FormField>
        <FormField name="password" control={control} label="Password" required>
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
        <Button type="submit" fullWidth>
          Sign in
        </Button>
      </form>
    </AuthShell>
  );
}
