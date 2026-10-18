import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertOctagon, RefreshCw } from 'lucide-react'

interface State {
  error: Error | null
}

/**
 * Last line of defence: an unexpected render error shows a recovery action instead of a
 * blank page, and the internal stack is never presented to the user.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Structured client log; the full stack stays out of the DOM.
    console.error('EarthScape render error', error.message, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children
    return (
      <div className="m-6 card border-alert-critical/40 bg-alert-critical/5 p-6" role="alert">
        <div className="flex items-start gap-3">
          <AlertOctagon className="mt-0.5 h-5 w-5 text-alert-critical" aria-hidden="true" />
          <div>
            <h2 className="text-sm font-semibold">This view could not be rendered</h2>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed muted">
              The interface hit an unexpected condition{this.props.label ? ` while loading ${this.props.label}` : ''}. Reloading usually recovers it; if it
              persists, report it through the support desk with the reference below.
            </p>
            <p className="mono mt-3 text-xs subtle">{this.state.error.message}</p>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => {
                  this.setState({ error: null })
                  window.location.reload()
                }}
              >
                <RefreshCw className="h-3.5 w-3.5" /> Reload
              </button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => this.setState({ error: null })}>
                Dismiss
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
