import {
  Activity,
  BarChart3,
  Boxes,
  Brain,
  Bug,
  ClipboardList,
  Cpu,
  Database,
  Gauge,
  Bell,
  FileText,
  GitCompare,
  LifeBuoy,
  LineChart,
  Map as MapIcon,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Store,
  Table2,
  Upload,
  UserIcon,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Capability } from "../../lib/api/types";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Capability required to view; omitted = any authenticated user. */
  capability?: Capability;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

/** The app's navigation contract, mirroring the spec's groups. */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "Overview",
    items: [
      { label: "Dashboard", to: "/app", icon: Gauge },
      { label: "Climate Map", to: "/app/map", icon: MapIcon },
    ],
  },
  {
    title: "Analytics",
    items: [
      { label: "Trends", to: "/app/trends", icon: Activity },
      { label: "Correlations", to: "/app/correlations", icon: BarChart3 },
      { label: "Anomaly Detection", to: "/app/anomalies", icon: Bug, capability: "anomaly.view" },
      { label: "Saved Analyses", to: "/app/analyses", icon: ClipboardList },
    ],
  },
  {
    title: "Data",
    items: [
      { label: "Observations", to: "/app/observations", icon: Table2 },
      { label: "Datasets", to: "/app/datasets", icon: Database, capability: "dataset.upload" },
      { label: "Upload Dataset", to: "/app/datasets/upload", icon: Upload, capability: "dataset.upload" },
      { label: "Quality", to: "/app/quality", icon: ShieldCheck },
      { label: "Ingestion", to: "/app/ingestion", icon: Boxes, capability: "ingestion.view" },
    ],
  },
  {
    title: "Processing",
    items: [
      { label: "Big Data Workspace", to: "/app/processing", icon: Cpu, capability: "processing.view" },
    ],
  },
  {
    title: "Models",
    items: [
      { label: "Machine Learning Lab", to: "/app/ml", icon: Brain, capability: "model.train" },
      { label: "Model Registry", to: "/app/models", icon: Store, capability: "prediction.view" },
      { label: "Forecast Explorer", to: "/app/forecasts", icon: LineChart, capability: "prediction.view" },
    ],
  },
  {
    title: "Operations",
    items: [
      { label: "Alerts & Notifications", to: "/app/alerts", icon: Bell },
      { label: "Reports & Exports", to: "/app/reports", icon: FileText },
      { label: "Support & Feedback", to: "/app/support", icon: LifeBuoy },
      { label: "Services", to: "/app/operations/services", icon: GitCompare, capability: "ops.logs" },
    ],
  },
  {
    title: "Administration",
    items: [
      { label: "Admin Overview", to: "/app/admin", icon: Gauge, capability: "ops.logs" },
      { label: "Users", to: "/app/admin/users", icon: Users, capability: "user.manage" },
      { label: "Roles & Access", to: "/app/admin/roles", icon: ShieldCheck, capability: "user.manage" },
      { label: "Dataset Admin", to: "/app/admin/datasets", icon: Database, capability: "dataset.admin" },
      { label: "Pipeline Admin", to: "/app/admin/pipeline", icon: Cpu, capability: "ops.logs" },
      { label: "Alert Rules", to: "/app/admin/alert-rules", icon: ShieldAlert, capability: "alert.rule.admin" },
      { label: "Audit Logs", to: "/app/admin/audit", icon: Table2, capability: "audit.view" },
      { label: "Settings", to: "/app/admin/settings", icon: Settings, capability: "settings.manage" },
      { label: "Tickets", to: "/app/admin/tickets", icon: LifeBuoy, capability: "support.manage" },
      { label: "System Health", to: "/app/operations/services", icon: Sparkles, capability: "ops.logs" },
    ],
  },
];
