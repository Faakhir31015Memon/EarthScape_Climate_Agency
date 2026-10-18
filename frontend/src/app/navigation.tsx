import type { ComponentType } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Boxes,
  BrainCircuit,
  ClipboardList,
  Cpu,
  Database,
  FileBarChart,
  Gauge,
  History,
  LifeBuoy,
  ListChecks,
  Map as MapIcon,
  MessageSquare,
  ScrollText,
  Settings,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  UploadCloud,
  UserCog,
  Users,
} from 'lucide-react'
import type { Role } from '../types/api'

export interface NavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
  roles?: Role[]
  capability?: string
  end?: boolean
  badge?: 'alerts' | 'notifications' | 'tickets'
}

export interface NavSection {
  id: string
  label: string
  items: NavItem[]
}

export const APPLICATION_SECTIONS: NavSection[] = [
  {
    id: 'monitor',
    label: 'Monitor',
    items: [
      { to: '/app', label: 'Overview dashboard', icon: Gauge, end: true },
      { to: '/app/analytics', label: 'Climate analytics', icon: BarChart3 },
      { to: '/app/history', label: 'Historical explorer', icon: History },
      { to: '/app/map', label: 'Geographic map', icon: MapIcon },
      { to: '/app/anomalies', label: 'Anomaly detection', icon: Activity },
    ],
  },
  {
    id: 'data',
    label: 'Data & pipelines',
    items: [
      { to: '/app/datasets', label: 'Dataset library', icon: Database },
      { to: '/app/uploads', label: 'Upload & validate', icon: UploadCloud, roles: ['administrator', 'climate_analyst'] },
      { to: '/app/ingestion', label: 'Ingestion jobs', icon: ListChecks, roles: ['administrator', 'climate_analyst'] },
      { to: '/app/processing', label: 'Processing monitor', icon: Boxes },
      { to: '/app/big-data', label: 'Big data workspace', icon: Cpu, roles: ['administrator', 'climate_analyst'] },
    ],
  },
  {
    id: 'insight',
    label: 'Models & forecasts',
    items: [
      { to: '/app/ml-lab', label: 'Machine learning lab', icon: BrainCircuit, roles: ['administrator', 'climate_analyst'] },
      { to: '/app/models', label: 'Model registry', icon: Sparkles },
      { to: '/app/forecasts', label: 'Forecast explorer', icon: TrendingUp },
    ],
  },
  {
    id: 'respond',
    label: 'Alerts & reporting',
    items: [
      { to: '/app/alerts', label: 'Alerts', icon: AlertTriangle, badge: 'alerts' },
      { to: '/app/notifications', label: 'Notification centre', icon: Bell, badge: 'notifications' },
      { to: '/app/reports', label: 'Reports & exports', icon: FileBarChart },
      { to: '/app/saved-analyses', label: 'Saved analyses', icon: ClipboardList },
    ],
  },
  {
    id: 'support',
    label: 'Account & support',
    items: [
      { to: '/app/profile', label: 'Profile & preferences', icon: UserCog },
      { to: '/app/support', label: 'Feedback & support', icon: LifeBuoy },
    ],
  },
]

export const ADMIN_SECTIONS: NavSection[] = [
  {
    id: 'administration',
    label: 'Administration',
    items: [
      { to: '/admin', label: 'Administrative overview', icon: ShieldCheck, roles: ['administrator'], end: true },
      { to: '/admin/users', label: 'User management', icon: Users, roles: ['administrator'] },
      { to: '/admin/roles', label: 'Roles & access', icon: UserCog, roles: ['administrator'] },
      { to: '/admin/datasets', label: 'Dataset administration', icon: Database, roles: ['administrator'] },
      { to: '/admin/pipelines', label: 'Pipelines & jobs', icon: Boxes, roles: ['administrator'] },
      { to: '/admin/alert-rules', label: 'Alert-rule administration', icon: AlertTriangle, roles: ['administrator'] },
      { to: '/admin/audit', label: 'Audit logs', icon: ScrollText, roles: ['administrator'] },
      { to: '/admin/health', label: 'System health', icon: Gauge, roles: ['administrator'] },
      { to: '/admin/settings', label: 'Configuration', icon: Settings, roles: ['administrator'] },
      { to: '/admin/tickets', label: 'Support tickets', icon: MessageSquare, roles: ['administrator'], badge: 'tickets' },
    ],
  },
]

export const PUBLIC_LINKS: { to: string; label: string }[] = [
  { to: '/', label: 'Home' },
  { to: '/about', label: 'About' },
  { to: '/capabilities', label: 'Platform' },
  { to: '/insights', label: 'Climate insights' },
  { to: '/help', label: 'Help' },
]
