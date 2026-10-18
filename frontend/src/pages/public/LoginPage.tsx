import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Eye, EyeOff, Loader2, LogIn } from 'lucide-react'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import { ErrorState, Field } from '../../components/ui'
import { LOGIN_LOCKOUT } from './components/publicData'

const loginSchema = z.object({
  email: z.string().min(1, 'Enter your email address.').email('Enter a valid email address.'),
  password: z.string().min(1, 'Enter your password.'),
})
type LoginValues = z.infer<typeof loginSchema>

/**
 * Codes surfaced by ``backend/apps/authentication/services.py`` (``credentials_invalid``,
 * ``account_locked``, ``account_disabled``). The verbatim API message is always shown;
 * this adds operator guidance for the cases the message alone leaves ambiguous.
 */
function guidanceFor(error: ApiRequestError): string | null {
  switch (error.code) {
    case 'credentials_invalid':
      return 'The email address and password are checked together, so a failure looks identical whether or not the account exists. Repeated failures temporarily lock the account.'
    case 'account_locked':
      return `The account is locked after too many failed attempts. It unlocks by itself after about ${LOGIN_LOCKOUT.minutes} minutes; an administrator can clear the lock sooner.`
    case 'account_disabled':
      return 'This account has been deactivated. An administrator can reactivate it from user management.'
    case 'email_not_verified':
    case 'email_unverified':
      return 'Sign-in is blocked until the address is confirmed. Check your inbox, or ask an administrator to mark the address verified.'
    default:
      return null
  }
}

export default function LoginPage() {
  const { login, configuration } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<ApiRequestError | null>(null)
  const [showPassword, setShowPassword] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema), mode: 'onSubmit' })

  // RequireAuth stores the intended destination in location.state.from; honour it after a successful sign-in.
  const from = (location.state as { from?: string } | null)?.from ?? '/app'
  const registrationOpen = configuration?.self_registration_enabled === true
  const resetDisabled = configuration?.password_reset_enabled === false

  const onSubmit = handleSubmit(async (values) => {
    setApiError(null)
    setSubmitting(true)
    try {
      await login(values.email.trim(), values.password)
      navigate(from, { replace: true })
    } catch (cause) {
      setApiError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setSubmitting(false)
    }
  })

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
        <input
          id="email"
          className="input"
          type="email"
          autoComplete="email"
          inputMode="email"
          spellCheck={false}
          autoFocus
          aria-invalid={Boolean(errors.email)}
          {...register('email')}
        />
      </Field>

      <Field label="Password" htmlFor="password" error={errors.password?.message} required>
        <div className="relative">
          <input
            id="password"
            className="input pr-10"
            type={showPassword ? 'text' : 'password'}
            autoComplete="current-password"
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

      {apiError ? (
        <div aria-live="assertive" className="space-y-2">
          <ErrorState error={apiError} />
          {guidanceFor(apiError) ? <p className="text-xs leading-relaxed muted">{guidanceFor(apiError)}</p> : null}
        </div>
      ) : null}

      <button type="submit" className="btn btn-primary w-full" disabled={submitting}>
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <LogIn className="h-4 w-4" aria-hidden="true" />}
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        {resetDisabled ? (
          <span className="muted">Self-service recovery is off here — an administrator issues reset links.</span>
        ) : (
          <Link to="/forgot-password" className="link">
            Forgot your password?
          </Link>
        )}
        {registrationOpen ? (
          <Link to="/register" className="link">
            Create an account
          </Link>
        ) : null}
      </div>

      <p className="text-xs leading-relaxed subtle">
        Your password is sent only to this deployment&rsquo;s API over the connection it is configured with, and is never displayed or stored by the browser.
      </p>
    </form>
  )
}
