import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Eye, EyeOff, Loader2, UserPlus } from 'lucide-react'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import { EmptyState, ErrorState, Field, Hint } from '../../components/ui'
import { useToast } from '../../components/ui/ToastProvider'
import { PasswordPolicyChecklist } from './components/PasswordPolicyChecklist'
import { passwordPolicySummary } from './components/publicData'
import { humanise } from '../../lib/format'

const registerSchema = z
  .object({
    full_name: z.string().min(1, 'Enter your full name.').max(160, 'Keep the name under 160 characters.'),
    email: z.string().min(1, 'Enter your email address.').email('Enter a valid email address.'),
    organisation: z.string().max(160, 'Keep the organisation under 160 characters.').optional(),
    password: z.string().min(10, 'Use at least 10 characters.'),
    confirm: z.string().min(1, 'Re-enter your password.'),
  })
  .refine((values) => values.password === values.confirm, { message: 'The two passwords do not match.', path: ['confirm'] })
type RegisterValues = z.infer<typeof registerSchema>

function guidanceFor(error: ApiRequestError): string | null {
  switch (error.code) {
    case 'registration_disabled':
      return 'Self-registration is closed here, so an administrator creates the account. Nothing was saved.'
    case 'email_taken':
      return 'An account already uses this address. Sign in, or recover the password.'
    case 'password_weak':
      return 'The password does not meet the policy above. The checklist here reads the same settings the server uses.'
    case 'role_not_permitted':
      return 'That role is not available through self-registration.'
    default:
      return null
  }
}

export default function RegisterPage() {
  const { register: createAccount, configuration } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const toast = useToast()
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<ApiRequestError | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema), mode: 'onSubmit' })

  const password = watch('password')
  const confirmValue = watch('confirm')
  const from = (location.state as { from?: string } | null)?.from ?? '/app'
  const registrationRoles = configuration?.registration_roles ?? []

  if (configuration?.self_registration_enabled === false) {
    return (
      <div className="space-y-5">
        <EmptyState
          title="Self-registration is closed here"
          description="Accounts are created by an administrator, who also decides what each one can do. Ask them for access, or sign in if you already have an account."
          action={
            <Link to="/login" className="btn btn-secondary btn-sm">
              Go to sign in
            </Link>
          }
        />
      </div>
    )
  }

  const onSubmit = handleSubmit(async (values) => {
    setApiError(null)
    setSubmitting(true)
    try {
      await createAccount({
        full_name: values.full_name.trim(),
        email: values.email.trim(),
        password: values.password,
        organisation: values.organisation?.trim() || undefined,
      })
      toast.success('Account created', 'You are signed in with the access this deployment grants new accounts.')
      navigate(from, { replace: true })
    } catch (cause) {
      setApiError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setSubmitting(false)
    }
  })

  return (
    <form onSubmit={onSubmit} className="space-y-5" noValidate>
      <Field label="Full name" htmlFor="full_name" error={errors.full_name?.message} required>
        <input
          id="full_name"
          className="input"
          autoComplete="name"
          autoFocus
          aria-invalid={Boolean(errors.full_name)}
          {...register('full_name')}
        />
      </Field>

      <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
        <input
          id="email"
          className="input"
          type="email"
          inputMode="email"
          autoComplete="email"
          spellCheck={false}
          aria-invalid={Boolean(errors.email)}
          {...register('email')}
        />
      </Field>

      <Field label="Organisation" htmlFor="organisation" error={errors.organisation?.message} hint="Optional. Shown as the source on datasets you contribute.">
        <input id="organisation" className="input" autoComplete="organization" aria-invalid={Boolean(errors.organisation)} {...register('organisation')} />
      </Field>

      <div>
        <Field label="Password" htmlFor="password" error={errors.password?.message} required>
          <div className="relative">
            <input
              id="password"
              className="input pr-10"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
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

      <Field label="Confirm password" htmlFor="confirm" error={errors.confirm?.message} required>
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
        id="register-password-rules"
      />

      <Hint>
        The server checks these rules again when you submit. Passwords are stored as hashes, so they can be replaced but never read back. Current
        policy: {passwordPolicySummary(configuration?.password_policy)}.
      </Hint>

      <Hint>
        {configuration
          ? `A new account here gets the ${registrationRoles.map(humanise).join(' or ') || 'viewer'} role. An administrator decides who moves beyond it.`
          : 'A new account gets the most limited role this deployment offers. An administrator grants anything wider.'}
      </Hint>

      {apiError ? (
        <div aria-live="assertive" className="space-y-2">
          <ErrorState error={apiError} />
          {guidanceFor(apiError) ? <p className="text-xs leading-relaxed muted">{guidanceFor(apiError)}</p> : null}
        </div>
      ) : null}

      <button type="submit" className="btn btn-primary w-full" disabled={submitting}>
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <UserPlus className="h-4 w-4" aria-hidden="true" />}
        {submitting ? 'Creating account…' : 'Create account'}
      </button>

      <p className="text-sm muted">
        Already have an account? <Link to="/login" className="link">Sign in</Link>
      </p>
    </form>
  )
}
