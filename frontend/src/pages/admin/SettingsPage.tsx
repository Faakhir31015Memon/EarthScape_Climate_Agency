import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Save, Undo2 } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  Field,
  Hint,
  KeyValue,
  Mono,
  PageHeader,
  Panel,
  RefreshButton,
  Select,
  Toggle,
  type Option,
} from '../../components/ui'
import { ActionFeedback, type Feedback } from './components/AdminBits'
import { describeError, type AdminError } from './components/adminUtils'
import { formatBytes, formatDate, humanise } from '../../lib/format'
import type { PlatformSettings } from '../../types/api'

/**
 * Choice-valued editable keys. These mirror `EDITABLE_SETTINGS` in
 * backend/apps/monitoring/views.py, where `default_theme` is declared as a
 * `choice` of light/dark/system. The GET /system/settings payload does not carry
 * the option list, so the one known choice key is declared here to render a
 * Select; if the server returns a value outside this set it is still shown.
 */
const CHOICE_KEYS: Record<string, string[]> = {
  default_theme: ['light', 'dark', 'system'],
}

type ControlKind = 'toggle' | 'number' | 'choice' | 'text'

function controlFor(setting: PlatformSettings): ControlKind {
  if (CHOICE_KEYS[setting.key]) return 'choice'
  if (typeof setting.value === 'boolean') return 'toggle'
  if (typeof setting.value === 'number') return 'number'
  return 'text'
}

function toDraft(setting: PlatformSettings): string {
  if (setting.value === null || setting.value === undefined) return ''
  return String(setting.value)
}

/**
 * Platform settings.
 *
 * Reads `GET /system/settings` and edits only the keys the server declares in
 * `editable_keys` (a strict whitelist). Edits are tracked locally and sent as one
 * `PATCH /system/settings` containing only the changed keys. Everything under
 * `env_derived` is read-only: the backend refuses to change security env keys
 * through this endpoint, so no control is ever rendered for them. The endpoint
 * returns only settings that already have a stored document, so a whitelisted key
 * with no stored value simply does not appear as editable yet.
 */
export default function SettingsPage() {
  const { can, hasRole } = useAuth()
  const canEdit = can('system:manage') && hasRole('administrator')

  const settings = useAsync(() => api.system.settings(), [])

  const [draft, setDraft] = useState<Record<string, string>>({})
  const [baseline, setBaseline] = useState<Record<string, string>>({})
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [saveError, setSaveError] = useState<AdminError | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!settings.data) return
    const editable = settings.data.settings.filter((setting) => settings.data!.editable_keys.includes(setting.key))
    const values: Record<string, string> = {}
    for (const setting of editable) values[setting.key] = toDraft(setting)
    setBaseline(values)
    setDraft(values)
  }, [settings.data])

  const editableSettings = useMemo<PlatformSettings[]>(() => {
    if (!settings.data) return []
    const keys = new Set(settings.data.editable_keys)
    return settings.data.settings.filter((setting) => keys.has(setting.key))
  }, [settings.data])

  const readOnlyStored = useMemo<PlatformSettings[]>(() => {
    if (!settings.data) return []
    const keys = new Set(settings.data.editable_keys)
    return settings.data.settings.filter((setting) => !keys.has(setting.key))
  }, [settings.data])

  const changedKeys = useMemo(
    () => editableSettings.filter((setting) => (draft[setting.key] ?? '') !== (baseline[setting.key] ?? '')).map((setting) => setting.key),
    [editableSettings, draft, baseline],
  )

  const setValue = useCallback((key: string, value: string) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setSaveError(null)
  }, [])

  const invalidNumberKeys = useMemo(
    () =>
      editableSettings
        .filter((setting) => controlFor(setting) === 'number')
        .map((setting) => setting.key)
        .filter((key) => {
          const raw = draft[key] ?? ''
          if (raw.trim() === '') return true
          const parsed = Number(raw)
          return !Number.isFinite(parsed) || !Number.isInteger(parsed)
        }),
    [editableSettings, draft],
  )

  const cancel = useCallback(() => {
    setDraft(baseline)
    setSaveError(null)
    setFeedback(null)
  }, [baseline])

  const save = useCallback(async () => {
    setBusy(true)
    setFeedback(null)
    setSaveError(null)
    const changes: Record<string, unknown> = {}
    for (const setting of editableSettings) {
      const key = setting.key
      if ((draft[key] ?? '') === (baseline[key] ?? '')) continue
      const kind = controlFor(setting)
      const raw = draft[key] ?? ''
      if (kind === 'toggle') changes[key] = raw === 'true'
      else if (kind === 'number') changes[key] = Number(raw)
      else changes[key] = raw
    }
    try {
      await api.system.updateSettings(changes)
      await settings.refetch()
      setFeedback({ tone: 'success', text: `Saved ${Object.keys(changes).length} setting${Object.keys(changes).length === 1 ? '' : 's'}. Values were re-read from the server.` })
    } catch (cause) {
      setSaveError(describeError(cause))
      setFeedback(null)
    } finally {
      setBusy(false)
    }
  }, [editableSettings, draft, baseline, settings])

  const envEntries = settings.data ? Object.entries(settings.data.env_derived) : []

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Platform settings"
        description="Whitelisted settings stored in the platform_settings collection. Security configuration from the environment is shown but never editable here."
        meta={
          settings.data ? (
            <>
              <Badge tone="neutral">{editableSettings.length} editable</Badge>
              <Badge tone="neutral">{envEntries.length} env-derived</Badge>
              {changedKeys.length > 0 ? <Badge tone="warning">{changedKeys.length} unsaved</Badge> : <span className="subtle">no pending changes</span>}
            </>
          ) : null
        }
        actions={<RefreshButton onClick={() => void settings.refetch()} busy={settings.refreshing} />}
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />

      {settings.error ? (
        <div className="mb-4">
          <Panel dense className="border-alert-critical/40">
            <div role="alert" className="text-sm text-alert-critical">Settings could not be loaded. {describeError(settings.error).message}</div>
          </Panel>
        </div>
      ) : null}

      {saveError ? (
        <div role="alert" className="mb-4 rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm">
          <p className="font-medium text-alert-critical">{saveError.message}</p>
          {Object.entries(saveError.fields).length > 0 ? (
            <ul className="mt-1 space-y-0.5 text-xs muted">
              {Object.entries(saveError.fields).map(([field, message]) => (
                <li key={field}>
                  <span className="mono">{field}</span>: {message}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-1 text-[11px] subtle">The server rejected the change; your edits are kept below so you can correct them.</p>
        </div>
      ) : null}

      {!canEdit ? (
        <div className="mb-4 rounded-xl border p-3 text-xs leading-relaxed muted" style={{ borderColor: 'var(--border)' }}>
          You do not have the <Mono>system:manage</Mono> capability as an administrator, so the controls below are
          disabled. The backend enforces this too: <Mono>PATCH /system/settings</Mono> is refused for non-administrators.
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel
          title="Editable settings"
          description="Each control matches the value type the server stores. Only changed keys are sent."
          dense
          className="lg:col-span-2"
          footer={
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="subtle">{changedKeys.length === 0 ? 'No changes to save.' : `${changedKeys.length} pending change${changedKeys.length === 1 ? '' : 's'}.`}</span>
              <div className="flex gap-2">
                <button type="button" className="btn btn-secondary btn-sm" onClick={cancel} disabled={!canEdit || busy || changedKeys.length === 0}>
                  <Undo2 className="h-3.5 w-3.5" aria-hidden="true" /> Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => void save()}
                  disabled={!canEdit || busy || changedKeys.length === 0 || invalidNumberKeys.length > 0}
                >
                  <Save className="h-3.5 w-3.5" aria-hidden="true" /> Save changes
                </button>
              </div>
            </div>
          }
        >
          {settings.loading && !settings.data ? <p className="text-sm muted">Loading settings…</p> : null}
          {settings.data && editableSettings.length === 0 ? (
            <p className="text-sm muted">No editable settings have been stored yet. The whitelist keys appear here once the backend has a stored value for them.</p>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            {editableSettings.map((setting) => {
              const kind = controlFor(setting)
              const keyId = `setting-${setting.key}`
              const value = draft[setting.key] ?? ''
              const dirty = value !== (baseline[setting.key] ?? '')
              const numberInvalid = kind === 'number' && invalidNumberKeys.includes(setting.key)
              const fieldError = numberInvalid ? 'Enter a whole number.' : saveError?.fields[setting.key] ?? null

              const choices = CHOICE_KEYS[setting.key]
              const options: Option[] | null = choices
                ? Array.from(new Set([...choices, ...(value && !choices.includes(value) ? [value] : [])])).map((c) => ({ value: c, label: humanise(c) }))
                : null

              let control: ReactNode
              if (kind === 'toggle') {
                control = <Toggle checked={value === 'true'} onChange={(next) => setValue(setting.key, next ? 'true' : 'false')} label={value === 'true' ? 'Enabled' : 'Disabled'} disabled={!canEdit} />
              } else if (kind === 'choice' && options) {
                control = <Select id={keyId} options={options} value={value} onChange={(next) => setValue(setting.key, next)} ariaLabel={setting.label ?? setting.key} disabled={!canEdit} />
              } else if (kind === 'number') {
                control = (
                  <input
                    id={keyId}
                    className="input"
                    type="number"
                    inputMode="numeric"
                    step="1"
                    value={value}
                    onChange={(event) => setValue(setting.key, event.target.value)}
                    disabled={!canEdit}
                    aria-invalid={numberInvalid}
                  />
                )
              } else {
                control = (
                  <textarea
                    id={keyId}
                    className="textarea"
                    rows={value.length > 60 ? 3 : 2}
                    value={value}
                    onChange={(event) => setValue(setting.key, event.target.value)}
                    disabled={!canEdit}
                  />
                )
              }

              return (
                <Field
                  key={setting.key}
                  label={<span className="flex items-center gap-2">{setting.label ?? humanise(setting.key)}{dirty ? <Badge tone="warning">changed</Badge> : null}</span>}
                  htmlFor={keyId}
                  error={fieldError}
                  hint={
                    <>
                      <span className="mono text-[11px]">{setting.key}</span>
                      {setting.description ? ` — ${setting.description}` : ''}
                      {setting.updated_at ? ` · last changed ${formatDate(setting.updated_at, true)}${setting.updated_by ? ` by ${setting.updated_by}` : ''}` : ' · never changed'}
                    </>
                  }
                >
                  {control}
                </Field>
              )
            })}
          </div>
        </Panel>

        <Panel title="Environment-derived (read-only)" description="Facts the backend reads straight from its configuration. They are diagnostics only; this endpoint refuses to modify security keys." dense>
          {settings.data ? (
            <KeyValue
              items={envEntries.map(([key, raw]) => ({
                label: humanise(key),
                value: <EnvValue keyName={key} value={raw} />,
              }))}
              columns={1}
            />
          ) : (
            <p className="text-sm muted">{settings.loading ? 'Loading…' : 'Unavailable.'}</p>
          )}
          <Hint>{settings.data?.note ?? 'Environment-backed values cannot be edited from the UI.'}</Hint>
        </Panel>

        {readOnlyStored.length > 0 ? (
          <Panel title="Stored but not editable" description="Settings that exist in the collection but are not on the editable whitelist." dense>
            <KeyValue
              items={readOnlyStored.map((setting) => ({
                label: setting.label ?? humanise(setting.key),
                value: <span className="break-all"><Mono>{String(setting.value)}</Mono> <span className="text-[11px] subtle">({setting.key})</span></span>,
              }))}
              columns={1}
            />
          </Panel>
        ) : null}
      </div>
    </div>
  )
}

function EnvValue({ keyName, value }: { keyName: string; value: unknown }) {
  if (typeof value === 'boolean') {
    return <Badge tone={value ? 'success' : 'neutral'} title={`${keyName} = ${value}`}>{value ? 'yes' : 'no'}</Badge>
  }
  if (keyName === 'max_upload_bytes' && typeof value === 'number') return <Mono>{formatBytes(value)}</Mono>
  if (typeof value === 'number') return <Mono>{value.toLocaleString()}</Mono>
  if (value === null || value === undefined || value === '') return <span className="text-xs subtle">not set</span>
  if (typeof value === 'string') {
    return value === 'true' || value === 'false' ? <Badge tone={value === 'true' ? 'success' : 'neutral'}>{value === 'true' ? 'yes' : 'no'}</Badge> : <Mono>{value}</Mono>
  }
  return <Mono>{JSON.stringify(value)}</Mono>
}
