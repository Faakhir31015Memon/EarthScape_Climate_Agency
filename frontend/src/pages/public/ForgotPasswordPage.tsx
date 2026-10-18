import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, CheckCircle2, Loader2, Mail, ShieldAlert } from 'lucide-react'
import { authApi } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAuth } from '../../features/auth/AuthContext'
import { ErrorState, Field, Panel } from '../../components/ui'

const forgotSchema = z.object({
  email: z.string().min(1, 'Enter your email address.').email('Enter a valid email address.'),
})
type ForgotValues = z.infer<typeof forgotSchema>

/**
 * ``begin_password_reset`` in ``backend/apps/authentication/services.py`` answers with a status
 * token. ``accepted`` (no account) and ``sent`` both mean "the normal, non-revealing reply";
 * ``configuration_required`` and ``delivery_failed`` mean nothing reached an inbox, so the page
 * says so plainly rather than implying an email is on its way.
 */
type Outcome = { kind: 'neutral' } | { kind: 'not_delivered'; text: string }

export default function ForgotPasswordPage() {
  const { configuration } = useAuth()
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<ApiRequestError | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<ForgotValues>({ resolver: zodResolver(forgotSchema), mode: 'onSubmit' })

  const resetDisabled = configuration?.password_reset_enabled === false

  const onSubmit = handleSubmit(async (values) => {
    setApiError(null)
    setOutcome(null)
    setSubmitting(true)
    try {
      const response = await authApi.requestPasswordReset(values.email.trim())
      if (response.status === 'configuration_required') {
        setOutcome({
          kind: 'not_delivered',
          text: 'Password recovery is implemented, but this deployment has no email transport configured, so no reset link was sent. Ask an administrator to reset your password from user management.',
        })
      } else if (response.status === 'delivery_failed') {
        setOutcome({
          kind: 'not_delivered',
          text: 'The recovery request was recorded, but the message could not be delivered. Try again shortly, or contact an administrator.',
        })
      } else {
        setOutcome({ kind: 'neutral' })
      }
    } catch (cause) {
      const error = cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0)
      if (error.code === 'feature_disabled') {
        setOutcome({
          kind: 'not_delivered',
          text: 'Self-service password recovery is disabled on this deployment. An administrator sets reset passwords directly.',
        })
      } else {
        setApiError(error)
      }
    } finally {
      setSubmitting(false)
    }
  })

  if (resetDisabled) {
    return (
      <Panel title="Recovery is issued by an administrator">
        <p className="text-sm leading-relaxed muted">
          Self-service password reset is switched off on this deployment, so this form cannot issue a link. An administrator resets passwords from the
          user-management area; sign in when they have done so.
        </p>
        <Link to="/login" className="link mt-4 inline-flex items-center gap-1.5 text-sm">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to sign in
        </Link>
      </Panel>
    )
  }

  if (outcome) {
    return (
      <div className="space-y-5">
        <div
          role="status"
          aria-live="polite"
          className="card-tight flex items-start gap-3"
          style={{ borderColor: outcome.kind === 'neutral' ? 'var(--border)' : undefined }}
        >
          {outcome.kind === 'neutral' ? (
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
          ) : (
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-solar-600 dark:text-solar-300" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <p className="text-sm font-semibold">
              {outcome.kind === 'neutral' ? 'Check your inbox' : 'No link was delivered'}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              {outcome.kind === 'neutral'
                ? 'If that address has an account, a single-use reset link has been sent. It expires after a short window; check your spam folder, then ask an administrator if nothing arrives.'
                : outcome.text}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {outcome.kind === 'not_delivered' ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOutcome(null)}>
              Try another address
            </button>
          ) : null}
          <Link to="/login" className="btn btn-primary btn-sm">
            Back to sign in
          </Link>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <p className="text-sm leading-relaxed muted">
        Enter the email address on your account. To avoid confirming which addresses exist, the response is the same whether or not an account is found.
      </p>

      <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
        <input
          id="email"
          className="input"
          type="email"
          inputMode="email"
          autoComplete="email"
          spellCheck={false}
          autoFocus
          aria-invalid={Boolean(errors.email)}
          {...register('email')}
        />
      </Field>

      {apiError ? <div aria-live="assertive"><ErrorState error={apiError} /></div> : null}

      <button type="submit" className="btn btn-primary w-full" disabled={submitting}>
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Mail className="h-4 w-4" aria-hidden="true" />}
        {submitting ? 'Sending…' : 'Request a reset link'}
      </button>

      <p className="text-sm muted">
        <Link to="/login" className="link inline-flex items-center gap-1.5">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to sign in
        </Link>
      </p>
    </form>
  )
}
