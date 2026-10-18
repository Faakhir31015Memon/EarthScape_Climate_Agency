import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { AppShell } from "../components/layout/AppShell";
import { RequireAuth } from "../components/layout/RequireAuth";
import { RequireCapability } from "../components/layout/RequireCapability";
import { PublicLayout } from "./pages/public/PublicLayout";

// Public
import { LandingPage } from "./pages/public/Landing";
import { AboutPage } from "./pages/public/About";
import { CapabilitiesPage } from "./pages/public/Capabilities";
import { InsightsPage } from "./pages/public/Insights";
import { HelpPage } from "./pages/public/Help";
import { PrivacyPage } from "./pages/public/Privacy";
import { TermsPage } from "./pages/public/Terms";
import { LoginPage } from "./pages/public/Login";
import { RegisterPage } from "./pages/public/Register";
import { ForgotPasswordPage } from "./pages/public/ForgotPassword";
import { ResetPasswordPage } from "./pages/public/ResetPassword";

// App
import { NotFoundPage } from "./pages/app/NotFound";
import { ForbiddenPage } from "./pages/app/Forbidden";
import { ProfilePage } from "./pages/app/Profile";
import { DashboardPage } from "./pages/app/Dashboard";
import { MapPage } from "./pages/app/MapPage";
import { TrendsPage } from "./pages/app/Trends";
import { CorrelationsPage } from "./pages/app/Correlations";
import { ObservationsPage } from "./pages/app/Observations";
import { DatasetsPage } from "./pages/app/Datasets";
import { QualityPage } from "./pages/app/Quality";
import { IngestionPage } from "./pages/app/Ingestion";
import { ServicesPage } from "./pages/app/Services";
import { AlertsCenterPage } from "./pages/app/AlertsCenter";
import { ReportsPage } from "./pages/app/Reports";
import { SupportPage } from "./pages/app/Support";
import { ProcessingPage } from "./pages/app/Processing";
import { MlLabPage } from "./pages/app/MlLab";
import { ModelRegistryPage } from "./pages/app/ModelRegistry";
import { ForecastsPage } from "./pages/app/Forecasts";
import { AnomaliesPage } from "./pages/app/Anomalies";
import { AnalysesPage } from "./pages/app/Analyses";
import { DatasetUploadPage } from "./pages/app/DatasetUpload";
import { DatasetDetailPage } from "./pages/app/DatasetDetail";
import { AdminOverviewPage } from "./pages/app/admin/AdminOverview";
import { AdminUsersPage } from "./pages/app/admin/Users";
import { AdminRolesPage } from "./pages/app/admin/Roles";
import { AdminDatasetsPage } from "./pages/app/admin/DatasetsAdmin";
import { AdminAlertRulesPage } from "./pages/app/admin/AlertRules";
import { AdminAuditLogsPage } from "./pages/app/admin/AuditLogs";
import { AdminSettingsPage } from "./pages/app/admin/Settings";
import { AdminTicketsPage } from "./pages/app/admin/Tickets";

import { useAuth } from "../features/auth/useAuth";

/** Bounce already-authenticated users away from the auth screens. */
function RedirectIfAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "authenticated") return <Navigate to="/app" replace />;
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      {/* Public marketing pages */}
      <Route path="/" element={<LandingPage />} />
      <Route element={<PublicLayout />}>
        <Route path="/about" element={<AboutPage />} />
        <Route path="/capabilities" element={<CapabilitiesPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/help" element={<HelpPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/terms" element={<TermsPage />} />
      </Route>

      {/* Auth screens */}
      <Route path="/login" element={<RedirectIfAuth><LoginPage /></RedirectIfAuth>} />
      <Route path="/register" element={<RedirectIfAuth><RegisterPage /></RedirectIfAuth>} />
      <Route path="/forgot-password" element={<RedirectIfAuth><ForgotPasswordPage /></RedirectIfAuth>} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      {/* Authenticated workspace */}
      <Route path="/app" element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route
            index
            element={
              <RequireCapability capability="platform.view">
                <DashboardPage />
              </RequireCapability>
            }
          />
          <Route path="map" element={<MapPage />} />
          <Route
            path="trends"
            element={
              <RequireCapability capability="platform.view">
                <TrendsPage />
              </RequireCapability>
            }
          />
          <Route
            path="correlations"
            element={
              <RequireCapability capability="platform.view">
                <CorrelationsPage />
              </RequireCapability>
            }
          />
          <Route path="observations" element={<ObservationsPage />} />
          <Route
            path="datasets"
            element={
              <RequireCapability capability="dataset.upload">
                <DatasetsPage />
              </RequireCapability>
            }
          />
          <Route path="quality" element={<QualityPage />} />
          <Route
            path="ingestion"
            element={
              <RequireCapability capability="ingestion.view">
                <IngestionPage />
              </RequireCapability>
            }
          />
          <Route
            path="operations/services"
            element={
              <RequireCapability capability="ops.logs">
                <ServicesPage />
              </RequireCapability>
            }
          />
          <Route
            path="processing"
            element={
              <RequireCapability capability="processing.view">
                <ProcessingPage />
              </RequireCapability>
            }
          />
          <Route
            path="ml"
            element={
              <RequireCapability capability="model.train">
                <MlLabPage />
              </RequireCapability>
            }
          />
          <Route
            path="models"
            element={
              <RequireCapability capability="prediction.view">
                <ModelRegistryPage />
              </RequireCapability>
            }
          />
          <Route
            path="forecasts"
            element={
              <RequireCapability capability="prediction.view">
                <ForecastsPage />
              </RequireCapability>
            }
          />
          <Route
            path="anomalies"
            element={
              <RequireCapability capability="anomaly.view">
                <AnomaliesPage />
              </RequireCapability>
            }
          />
          <Route path="analyses" element={<AnalysesPage />} />
          <Route
            path="datasets/upload"
            element={
              <RequireCapability capability="dataset.upload">
                <DatasetUploadPage />
              </RequireCapability>
            }
          />
          <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
          <Route
            path="alerts"
            element={
              <RequireCapability capability="platform.view">
                <AlertsCenterPage />
              </RequireCapability>
            }
          />
          <Route
            path="reports"
            element={
              <RequireCapability capability="platform.view">
                <ReportsPage />
              </RequireCapability>
            }
          />
          <Route path="support" element={<SupportPage />} />
          <Route
            path="admin"
            element={
              <RequireCapability capability="ops.logs">
                <AdminOverviewPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/users"
            element={
              <RequireCapability capability="user.manage">
                <AdminUsersPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/roles"
            element={
              <RequireCapability capability="user.manage">
                <AdminRolesPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/datasets"
            element={
              <RequireCapability capability="dataset.admin">
                <AdminDatasetsPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/pipeline"
            element={
              <RequireCapability capability="ops.logs">
                <ProcessingPage admin />
              </RequireCapability>
            }
          />
          <Route
            path="admin/alert-rules"
            element={
              <RequireCapability capability="alert.rule.admin">
                <AdminAlertRulesPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/audit"
            element={
              <RequireCapability capability="audit.view">
                <AdminAuditLogsPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/settings"
            element={
              <RequireCapability capability="settings.manage">
                <AdminSettingsPage />
              </RequireCapability>
            }
          />
          <Route
            path="admin/tickets"
            element={
              <RequireCapability capability="support.manage">
                <AdminTicketsPage />
              </RequireCapability>
            }
          />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="forbidden" element={<ForbiddenPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>

      {/* Global fallback */}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
