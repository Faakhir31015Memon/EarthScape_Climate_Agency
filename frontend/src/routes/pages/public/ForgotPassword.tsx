import { useState } from "react";
import { Link } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { CheckCircle2 } from "lucide-react";
import { AuthShell } from "../../../components/layout/AuthShell";
import { Button } from "../../../components/ui/Button";
import { ErrorState } from "../../../components/ui/EmptyState";
import { FormField } from "../../../components/ui/FormField";
import { Input } from "../../../components/ui/Input";
import { useAuth } from "../../../features/auth/useAuth";
import { isApiError } from "../../../lib/api/client";

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
});

type FormValues = z.infer<typeof schema>;

export function ForgotPasswordPage() {
  const { requestPasswordReset } = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [details, setDetails] = useState<string[]>([]);

  const { control, handleSubmit } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "" },
  });

  const submit = handleSubmit(async (values) => {
    setMessage(null);
    setServerMessage(null);
    setDetails([]);
    try {
      await requestPasswordReset(values.email);
      setMessage("If an account matches that email, a recovery link has been sent.");
    } catch (err) {
      if (isApiError(err)) {
        setServerMessage(err.message);
        setDetails(err.details);
      } else {
        setServerMessage("We could not process that request. Try again.");
      }
    }
  });

  return (
    <AuthShell
      title="Reset your password"
      subtitle="Request a recovery link for your account."
      documentTitle="Forgot password"
      footer={
        <Link to="/login" className="font-medium text-primary hover:underline">
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        {serverMessage ? (
          <ErrorState title="Recovery unavailable" message={serverMessage} details={details} />
        ) : null}
        {message ? (
          <p className="flex items-start gap-2 rounded-lg border border-success/40 bg-success/10 px-3 py-2 text-sm text-content">
            <CheckCircle2 size={16} className="mt-0.5 text-success" aria-hidden="true" />
            {message}
          </p>
        ) : null}
        <FormField name="email" control={control} label="Email" required>
          {({ id, value, onChange, onBlur, invalid, describedBy }) => (
            <Input
              id={id}
              type="email"
              inputMode="email"
              autoComplete="email"
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
          Send recovery link
        </Button>
      </form>
    </AuthShell>
  );
}
