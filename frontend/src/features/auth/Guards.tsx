import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './AuthContext'
import type { Role } from '../../types/api'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ShieldX } from 'lucide-react'

export function Can({ capability, roles, fallback = null, children }: {
  capability?: string
  roles?: Role[]
  fallback?: ReactNode
  children: ReactNode
}) {
  const { can, hasRole, account } = useAuth()
  if (!account) return <>{fallback}</>
  const allowed = (capability ? can(capability) : true) && (roles ? hasRole(...roles) : true)
  return <>{allowed ? children : fallback}</>
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') {
    return (
      <div className="min-h-[60vh] p-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="mt-4 h-40" />
      </div>
    )
  }
  if (status === 'anonymous') return <Navigate to="/login" state={{ from: location.pathname }} replace />
  return <>{children}</>
}

export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { status, hasRole, account } = useAuth()
  if (status === 'loading') return <Skeleton className="m-8 h-40" />
  if (status === 'anonymous') return <Navigate to="/login" replace />
  if (!hasRole(...roles)) {
    return (
      <EmptyState
        icon={<ShieldX className="h-6 w-6" />}
        title="Your role cannot open this area"
        description={
          account
            ? `This module is limited to ${roles.join(', ')}. Sign in with an account that has the required role.`
            : 'Sign in with an account that has the required role.'
        }
      />
    )
  }
  return <>{children}</>
}
