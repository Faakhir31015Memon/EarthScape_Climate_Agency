import {
  Activity,
  BarChart3,
  Boxes,
  Cpu,
  Database,
  Globe2,
  LayoutDashboard,
  Map as MapIcon,
  ShieldAlert,
  ShieldCheck,
  Table2,
  User as UserIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Capability } from "../../lib/api/types";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Capability required to view; omitted = any authenticated user. */
  capability?: Capability;
  /**
   * Route not implemented in this foundation phase. Rendered disabled so the
   * shell reflects the intended information architecture without dead links;
   * later phases flip these to live routes.
   */
  planned?: boolean;
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
      { label: "Dashboard", to: "/app", icon: LayoutDashboard },
      { label: "Climate Map", to: "/app/map", icon: MapIcon },
    ],
  },
  {
    title: "Analytics",
    items: [
      { label: "Trends", to: "/app/trends", icon: Activity },
      { label: "Correlations", to: "/app/correlations", icon: BarChart3 },
      { label: "Saved Analyses", to: "/app/analyses", icon: BarChart3, capability: "analysis.save", planned: true },
    ],
  },
  {
    title: "Data",
    items: [
      { label: "Observations", to: "/app/observations", icon: Table2 },
      { label: "Datasets", to: "/app/datasets", icon: Database, capability: "dataset.upload" },
      { label: "Quality", to: "/app/quality", icon: ShieldCheck },
    ],
  },
  {
    title: "Processing",
    items: [
      { label: "Ingestion", to: "/app/ingestion", icon: Boxes, capability: "ingestion.view" },
      { label: "Processing Jobs", to: "/app/processing", icon: Cpu, capability: "processing.view", planned: true },
    ],
  },
  {
    title: "Models",
    items: [
      { label: "Predictions", to: "/app/models", icon: Activity, capability: "prediction.view", planned: true },
    ],
  },
  {
    title: "Operations",
    items: [
      { label: "Services", to: "/app/operations/services", icon: Globe2, capability: "ops.logs" },
      { label: "Alerts", to: "/app/alerts", icon: ShieldAlert, capability: "alert.acknowledge", planned: true },
    ],
  },
  {
    title: "Administration",
    items: [
      { label: "Users", to: "/app/users", icon: UserIcon, capability: "user.manage", planned: true },
    ],
  },
];
