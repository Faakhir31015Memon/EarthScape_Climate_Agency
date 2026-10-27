import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { CheckCircle2, Eye, EyeOff, KeyRound, Loader2 } from 'lucide-react'
import { anonymousPost, ApiRequestError } from '../../services/http'
import { useAuth } from '../../features/auth/AuthContext'
import { EmptyState, ErrorState, Field } from '../../components/ui'
import { PasswordPolicyChecklist } from './components/PasswordPolicyChecklist'

const resetSchema = z
  .object({
    password: z.string().min(10, 'Use at least 10 characters.'),
    confirm: z.string().min(1, 'Re-enter your new password.'),
  })
  .refine((values) => values.password === values.confirm, { message: 'The two passwords do not match.', path: ['confirm'] })
type ResetValues = z.infer<typeof resetSchema>

function guidanceFor(error: ApiRequestError): string | null {
  switch (error.code) {
    case 'reset_invalid':
    case 'reset_used':
      return 'That link has already been used. Each one works a single time, so request a new one.'
    case 'reset_expired':
      return 'This reset link has expired. Request a new one from the recovery page.'
    case 'password_weak':
      return 'The new password does not meet the policy shown above.'
    default:
      return null
  }
}

export default function ResetPasswordPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { configuration } = useAuth()
  const token = searchParams.get('token')?.trim() ?? ''

  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<ApiRequestError | null>(null)
  const [done, setDone] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors },
  } = useForm<ResetValues>({ resolver: zodResolver(resetSchema), mode: 'onSubmit' })

  const password = watch('password')
  const confirmValue = watch('confirm')

  const onSubmit = handleSubmit(async (values) => {
    setApiError(null)
    setSubmitting(true)
    try {
      // The typed client's confirmPasswordReset() posts { token, password }, but the backend
      // PasswordResetConfirmSerializer requires { token, new_password }. Posting through the
      // shared anonymous helper avoids that field-name mismatch without editing the read-only client.
      await anonymousPost<{ status: string }>('/auth/password-reset/confirm', { token, new_password: values.password })
      setSearchParams({}, { replace: true })
      setDone(true)
    } catch (cause) {
      setApiError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setSubmitting(false)
    }
  })

  if (!token) {
    return (
      <EmptyState
        title="This reset link is incomplete"
        description="Recovery links carry a single-use token in the address. This page did not get one, so there is nothing to confirm yet. Request a new link and open the address in full."
        action={
          <Link to="/forgot-password" className="btn btn-primary btn-sm">
            Request a new link
          </Link>
        }
      />
    )
  }

  if (done) {
    return (
      <div className="space-y-5">
        <div role="status" aria-live="polite" className="card-tight flex items-start gap-3">
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">Password updated</p>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              The link is used up, and every other session on the account has been signed out. Sign in with your new password.
            </p>
          </div>
        </div>
        <Link to="/login" className="btn btn-primary w-full">
          Go to sign in
        </Link>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <p className="text-sm leading-relaxed muted">
        Set a new password for the account this link belongs to. Once it is saved, the link stops working.
      </p>

      <div>
        <Field label="New password" htmlFor="password" error={errors.password?.message} required>
          <div className="relative">
            <input
              id="password"
              className="input pr-10"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              autoFocus
              aria-invalid={Boolean(errors.password)}
              {...register('password')}
            />
            <button
              type="button"
              className="btn btn-ghost btn-icon absolute right-0 top-1/2 -translate-y-1/2"
              onClick={() => setShowPassword((current) => !current)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              aria-pressed={showPassword}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </Field>
      </div>

      <Field label="Confirm new password" htmlFor="confirm" error={errors.confirm?.message} required>
        <div className="relative">
          <input
            id="confirm"
            className="input pr-10"
            type={showConfirm ? 'text' : 'password'}
            autoComplete="new-password"
            aria-invalid={Boolean(errors.confirm)}
            {...register('confirm')}
          />
          <button
            type="button"
            className="btn btn-ghost btn-icon absolute right-0 top-1/2 -translate-y-1/2"
            onClick={() => setShowConfirm((current) => !current)}
            aria-label={showConfirm ? 'Hide confirmation password' : 'Show confirmation password'}
            aria-pressed={showConfirm}
          >
            {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
      </Field>

      <PasswordPolicyChecklist
        value={password ?? ''}
        confirm={confirmValue ?? ''}
        policy={configuration?.password_policy}
        id="reset-password-rules"
      />

      {apiError ? (
        <div aria-live="assertive" className="space-y-2">
          <ErrorState error={apiError} />
          {guidanceFor(apiError) ? <p className="text-xs leading-relaxed muted">{guidanceFor(apiError)}</p> : null}
        </div>
      ) : null}

      <button type="submit" className="btn btn-primary w-full" disabled={submitting}>
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
        {submitting ? 'Updating…' : 'Set new password'}
      </button>

      <p className="text-sm muted">
        Link expired or already used? <Link to="/forgot-password" className="link">Request a new reset link</Link>
      </p>
    </form>
  )
}
