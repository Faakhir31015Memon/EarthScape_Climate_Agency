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
type Outcome = { kind: 'neutral'; email?: string } | { kind: 'not_delivered'; text: string }

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
    const email = values.email.trim()
    try {
      const response = await authApi.requestPasswordReset(email)
      if (response.status === 'admin_restricted') {
        setOutcome({
          kind: 'not_delivered',
          text: response.note || 'Password reset via email is strictly for user accounts. Administrator accounts cannot be reset using this form.',
        })
      } else if (response.status === 'configuration_required') {
        setOutcome({
          kind: 'not_delivered',
          text: 'Recovery email is not switched on for this site yet, so no link could be sent. Nothing has changed on your account.',
        })
      } else if (response.status === 'delivery_failed') {
        setOutcome({
          kind: 'not_delivered',
          text: 'Your request was recorded, but the email could not be delivered. Please verify your email address and try again in a moment.',
        })
      } else {
        setOutcome({ kind: 'neutral', email })
      }
    } catch (cause) {
      const error = cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0)
      if (error.code === 'feature_disabled') {
        setOutcome({
          kind: 'not_delivered',
          text: 'Self-service password recovery is switched off on this site.',
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
      <Panel title="Password recovery is switched off">
        <p className="text-sm leading-relaxed muted">
          Self-service reset is turned off on this site, so this form cannot send a link.
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
              {outcome.kind === 'neutral' ? 'Reset link sent to your email' : 'No link was delivered'}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              {outcome.kind === 'neutral'
                ? `A password reset link has been automatically sent to ${outcome.email ? `"${outcome.email}"` : 'your email address'}. Open your inbox, click the reset link, and you will be able to set a new password immediately. (Remember to check your spam folder if it doesn't appear in a few moments).`
                : outcome.text}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOutcome(null)}>
            Send another link
          </button>
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
        Enter your registered email address below. We'll automatically send a secure password reset link to your email.
      </p>

      <Field label="Email address" htmlFor="email" error={errors.email?.message} required hint="The email associated with your user account.">
        <input
          id="email"
          className="input"
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="your.email@example.com"
          spellCheck={false}
          autoFocus
          aria-invalid={Boolean(errors.email)}
          {...register('email')}
        />
      </Field>

      {apiError ? <div aria-live="assertive"><ErrorState error={apiError} /></div> : null}

      <button type="submit" className="btn btn-primary w-full" disabled={submitting}>
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Mail className="h-4 w-4" aria-hidden="true" />}
        {submitting ? 'Sending reset link…' : 'Send Password Reset Link'}
      </button>

      <p className="text-sm muted">
        <Link to="/login" className="link inline-flex items-center gap-1.5">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back to sign in
        </Link>
      </p>
    </form>
  )
}
