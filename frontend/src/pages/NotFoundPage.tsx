import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { PUBLIC_LINKS } from '../app/navigation'

export function NotFoundPage() {
  return (
    <div className="mx-auto flex max-w-[720px] flex-col items-start px-5 py-24">
      <span className="badge badge-info">
        <Compass className="h-3 w-3" /> 404
      </span>
      <h1 className="mt-4 font-display text-3xl font-semibold">This route is not part of the platform</h1>
      <p className="mt-3 text-sm leading-relaxed muted">
        The address you requested does not map to a module in EarthScape. Every functional area is reachable from the navigation below, or from the dashboard
        once you are signed in.
      </p>
      <ul className="mt-6 flex flex-wrap gap-2">
        {PUBLIC_LINKS.map((link) => (
          <li key={link.to}>
            <Link to={link.to} className="btn btn-secondary btn-sm">
              {link.label}
            </Link>
          </li>
        ))}
        <li>
          <Link to="/login" className="btn btn-primary btn-sm">
            Sign in
          </Link>
        </li>
      </ul>
    </div>
  )
}

export default NotFoundPage
