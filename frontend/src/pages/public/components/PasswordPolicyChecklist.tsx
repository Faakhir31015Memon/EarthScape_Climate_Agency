import { Check, X } from 'lucide-react'
import type { AuthConfiguration } from '../../../types/api'
import { passwordRules } from './publicData'

/**
 * Live strength checklist. The rules come from ``api.auth.configuration()`` so the
 * form never contradicts the server; the server remains authoritative because the
 * password is re-validated by ``backend/common/security.py`` on submission.
 */
export function PasswordPolicyChecklist({
  value,
  policy,
  id = 'password-rules',
  confirm,
}: {
  value: string
  policy?: AuthConfiguration['password_policy'] | null
  id?: string
  /** When supplied, a live "passwords match" check is appended for the confirm field. */
  confirm?: string
}) {
  const rules = passwordRules(policy)
  const renderItem = (key: string, satisfied: boolean, neutral: boolean, label: string) => (
    <li key={key} className="flex items-center gap-1.5 text-xs">
      <span
        className={
          satisfied
            ? 'flex h-4 w-4 items-center justify-center rounded-full bg-forest-600 text-white'
            : neutral
              ? 'flex h-4 w-4 items-center justify-center rounded-full border text-[var(--text-subtle)]'
              : 'flex h-4 w-4 items-center justify-center rounded-full bg-alert-critical/15 text-alert-critical'
        }
        style={neutral ? { borderColor: 'var(--border-strong)' } : undefined}
        aria-hidden="true"
      >
        {neutral ? null : satisfied ? <Check className="h-2.5 w-2.5" /> : <X className="h-2.5 w-2.5" />}
      </span>
      <span className="muted">{label}</span>
      <span className="sr-only">{satisfied ? ' (met)' : neutral ? ' (not checked yet)' : ' (not met)'}</span>
    </li>
  )
  return (
    <div className="mt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide subtle">Password policy</p>
      <ul id={id} className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1.5">
        {rules.map((rule) =>
          renderItem(rule.id, value.length > 0 && rule.satisfied(value), value.length === 0, rule.label),
        )}
        {confirm === undefined
          ? null
          : renderItem(
              'match',
              confirm.length > 0 && confirm === value,
              confirm.length === 0,
              'Passwords match',
            )}
      </ul>
    </div>
  )
}

export default PasswordPolicyChecklist
