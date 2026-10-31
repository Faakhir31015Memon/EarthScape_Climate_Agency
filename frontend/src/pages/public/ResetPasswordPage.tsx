import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { AlertOctagon, CheckCircle2, Eye, EyeOff, KeyRound, Loader2, ShieldAlert } from 'lucide-react'
import { authApi } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAuth } from '../../features/auth/AuthContext'
import { EmptyState, Field } from '../../components/ui'
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
      return 'This recovery link is not valid. Request a fresh link from the recovery page.'
    case 'reset_used':
      return 'That recovery link has already been used. Each link works only once for your security.'
    case 'reset_expired':
      return 'This reset link has expired. Request a new link from the recovery page.'
    case 'password_weak':
      return 'The new password does not meet the requirements shown below.'
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
      await authApi.confirmPasswordReset(token, values.password)
      setSearchParams({}, { replace: true })
      setDone(true)
    } catch (cause) {
      setApiError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setSubmitting(false)
    }
  })

  const isLinkError = Boolean(
    apiError && (apiError.code === 'reset_used' || apiError.code === 'reset_invalid' || apiError.code === 'reset_expired')
  )

  if (isLinkError && apiError) {
    return (
      <div className="space-y-5">
        <div role="alert" className="card-tight flex items-start gap-3 border-solar-500/30 bg-solar-500/5">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-solar-600 dark:text-solar-400" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">
              {apiError.code === 'reset_expired' ? 'This reset link has expired' : 'This reset link has already been used'}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              {apiError.code === 'reset_expired'
                ? 'Password recovery links are valid for 30 minutes and this one has expired. Please request a new link.'
                : 'This password reset link has already been used and cannot be reused. Each recovery link works only once for your security.'}
            </p>
          </div>
        </div>
        <Link to="/forgot-password" className="btn btn-primary w-full">
          Request a new reset link
        </Link>
      </div>
    )
  }

  if (!token && !done) {
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
            <p className="text-sm font-semibold">Password updated successfully</p>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              Your password has been changed. For security, every previous session on your account has been signed out. You can now sign in with your new password.
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
        Set a new password for your account. Once updated, this reset link stops working.
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
        <div role="alert" aria-live="assertive" className="card-tight flex items-start gap-3 border-alert-critical/30 bg-alert-critical/5">
          <AlertOctagon className="mt-0.5 h-5 w-5 shrink-0 text-alert-critical" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">Could not update password</p>
            <p className="mt-1 text-sm leading-relaxed muted">
              {guidanceFor(apiError) || apiError.message}
            </p>
          </div>
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
