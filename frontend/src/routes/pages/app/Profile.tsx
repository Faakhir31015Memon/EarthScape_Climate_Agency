import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { KeyRound } from "lucide-react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Button } from "../../../components/ui/Button";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { FormField } from "../../../components/ui/FormField";
import { Input } from "../../../components/ui/Input";
import { DataTable, type Column } from "../../../components/ui/DataTable";
import { useToast } from "../../../components/ui/Toast";
import { ChangePasswordDialog } from "../../../features/auth/ChangePasswordDialog";
import { useAuth } from "../../../features/auth/useAuth";
import { useApi } from "../../../lib/api/query";
import { formatDateUtc } from "../../../lib/format";
import { isApiError } from "../../../lib/api/client";
import type { MeResult, SessionInfo } from "../../../lib/api/types";

const schema = z.object({
  full_name: z.string().min(2, "Enter your full name"),
});
type ProfileValues = z.infer<typeof schema>;

const sessionColumns: Column<SessionInfo>[] = [
  {
    key: "issued",
    header: "Signed in",
    render: (s) => <span>{formatDateUtc(s.issued_at)}</span>,
    sortValue: (s) => s.issued_at,
  },
  {
    key: "expires",
    header: "Expires",
    render: (s) => <span>{formatDateUtc(s.expires_at)}</span>,
    sortValue: (s) => s.expires_at,
  },
  { key: "device", header: "Device", render: (s) => <span className="font-mono text-xs">{s.device_hint}</span> },
];

export function ProfilePage() {
  const { user, updateProfile } = useAuth();
  const toast = useToast();
  const [pwOpen, setPwOpen] = useState(false);
  const me = useApi<MeResult>("/auth/me", []);

  const { control, handleSubmit } = useForm<ProfileValues>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: user?.full_name ?? "" },
  });

  const submit = handleSubmit(async (values) => {
    try {
      await updateProfile({ full_name: values.full_name });
      toast.success("Profile updated");
    } catch (err) {
      toast.error(isApiError(err) ? err.message : "Could not update profile");
    }
  });

  if (!user) return null;

  return (
    <div className="space-y-6">
      <PageHeader title="Your profile" eyebrow="Account" description="Personal details, security and active sessions." />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Account details" />
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Name</dt>
              <dd className="text-content">{user.full_name}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Email</dt>
              <dd className="text-content">{user.email}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Role</dt>
              <dd>
                <Badge tone={user.role === "administrator" ? "primary" : "info"}>
                  {user.role.replace("_", " ")}
                </Badge>
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Member since</dt>
              <dd className="text-content">{formatDateUtc(user.created_at)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Last sign-in</dt>
              <dd className="text-content">{formatDateUtc(user.last_login_at)}</dd>
            </div>
          </dl>
        </Card>

        <Card>
          <CardHeader title="Security" action={
            <Button size="sm" variant="outline" leftIcon={<KeyRound size={16} />} onClick={() => setPwOpen(true)}>
              Change password
            </Button>
          } />
          <p className="text-sm text-muted">
            {user.must_change_password
              ? "Your account still uses its initial password and must be rotated on next sign-in."
              : "Your password meets the current policy."}
          </p>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Edit display name" />
          <form onSubmit={submit} className="space-y-4">
            <FormField name="full_name" control={control} label="Full name" required>
              {({ id, value, onChange, onBlur, invalid, describedBy }) => (
                <Input id={id} value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} invalid={invalid} autoComplete="name" />
              )}
            </FormField>
            <Button type="submit" loading={false}>
              Save changes
            </Button>
          </form>
        </Card>

        <Card>
          <CardHeader title="Capabilities" subtitle="Granted by your role and enforced by the server." />
          {me.loading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {(me.data?.capabilities ?? []).map((c) => (
                <Badge key={c} tone="neutral" className="font-mono text-xs">
                  {c}
                </Badge>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title="Active sessions" subtitle="Refresh tokens issued to this account." />
        <DataTable<SessionInfo>
          columns={sessionColumns}
          rows={me.data?.sessions ?? []}
          rowKey={(s) => s.id}
          loading={me.loading}
          error={me.error}
          onRetry={me.refetch}
          emptyTitle="No active sessions"
        />
      </Card>

      <ChangePasswordDialog open={pwOpen} onClose={() => setPwOpen(false)} />
    </div>
  );
}
