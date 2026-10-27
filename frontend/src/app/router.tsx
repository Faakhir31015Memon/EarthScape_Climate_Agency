import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppLayout } from '../layouts/AppLayout'
import { AuthLayout } from '../layouts/AuthLayout'
import { PublicLayout } from '../layouts/PublicLayout'
import { RequireAuth, RequireRole } from '../features/auth/Guards'
import { PageLoader } from '../components/PageLoader'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { NotFoundPage } from '../pages/NotFoundPage'

const LandingPage = lazy(() => import('../pages/public/LandingPage'))
const HelpPage = lazy(() => import('../pages/public/HelpPage'))
const LoginPage = lazy(() => import('../pages/public/LoginPage'))
const RegisterPage = lazy(() => import('../pages/public/RegisterPage'))
const ForgotPasswordPage = lazy(() => import('../pages/public/ForgotPasswordPage'))
const ResetPasswordPage = lazy(() => import('../pages/public/ResetPasswordPage'))

const DashboardPage = lazy(() => import('../pages/app/DashboardPage'))
const AnalyticsPage = lazy(() => import('../pages/app/AnalyticsPage'))
const HistoryPage = lazy(() => import('../pages/app/HistoryPage'))
const MapPage = lazy(() => import('../pages/app/MapPage'))
const AnomaliesPage = lazy(() => import('../pages/app/AnomaliesPage'))
const DatasetsPage = lazy(() => import('../pages/app/DatasetsPage'))
const DatasetDetailPage = lazy(() => import('../pages/app/DatasetDetailPage'))
const UploadPage = lazy(() => import('../pages/app/UploadPage'))
const IngestionJobsPage = lazy(() => import('../pages/app/IngestionJobsPage'))
const ProcessingMonitorPage = lazy(() => import('../pages/app/ProcessingMonitorPage'))
const BigDataPage = lazy(() => import('../pages/app/BigDataPage'))
const MlLabPage = lazy(() => import('../pages/app/MlLabPage'))
const ModelRegistryPage = lazy(() => import('../pages/app/ModelRegistryPage'))
const ForecastExplorerPage = lazy(() => import('../pages/app/ForecastExplorerPage'))
const AlertsPage = lazy(() => import('../pages/app/AlertsPage'))
const NotificationsPage = lazy(() => import('../pages/app/NotificationsPage'))
const ReportsPage = lazy(() => import('../pages/app/ReportsPage'))
const ProfilePage = lazy(() => import('../pages/app/ProfilePage'))
const SupportPage = lazy(() => import('../pages/app/SupportPage'))

const AdminOverviewPage = lazy(() => import('../pages/admin/AdminOverviewPage'))
const UsersPage = lazy(() => import('../pages/admin/UsersPage'))
const RolesPage = lazy(() => import('../pages/admin/RolesPage'))
const DatasetAdminPage = lazy(() => import('../pages/admin/DatasetAdminPage'))
const PipelineAdminPage = lazy(() => import('../pages/admin/PipelineAdminPage'))
const AlertRulesPage = lazy(() => import('../pages/admin/AlertRulesPage'))
const SystemHealthPage = lazy(() => import('../pages/admin/SystemHealthPage'))
const TicketAdminPage = lazy(() => import('../pages/admin/TicketAdminPage'))

export function AppRouter() {
  return (
    <BrowserRouter>
      <RoutedViews />
    </BrowserRouter>
  )
}

/**
 * Keyed by pathname so one view's render error cannot keep showing its fallback after the
 * operator navigates: an unkeyed boundary holds its error state across every later route.
 */
function RoutedViews() {
  const location = useLocation()
  return (
    <ErrorBoundary key={location.pathname}>
      <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route element={<PublicLayout />}>
              <Route index element={<LandingPage />} />
              <Route path="help" element={<HelpPage />} />
            </Route>

            <Route path="/login" element={<AuthLayout title="Sign in" subtitle="Use the account your administrator gave you." />}>
              <Route index element={<LoginPage />} />
            </Route>
            <Route path="/register" element={<AuthLayout title="Create an account" subtitle="Registration follows the deployment policy configured by your administrator." />}>
              <Route index element={<RegisterPage />} />
            </Route>
            <Route path="/forgot-password" element={<AuthLayout title="Recover access" subtitle="Request a single-use password reset link." />}>
              <Route index element={<ForgotPasswordPage />} />
            </Route>
            <Route path="/reset-password" element={<AuthLayout title="Set a new password" subtitle="Confirm the reset token and choose a new password." />}>
              <Route index element={<ResetPasswordPage />} />
            </Route>

            <Route
              path="/app"
              element={
                <RequireAuth>
                  <AppLayout />
                </RequireAuth>
              }
            >
              <Route index element={<DashboardPage />} />
              <Route path="analytics" element={<AnalyticsPage />} />
              <Route path="history" element={<HistoryPage />} />
              <Route path="map" element={<MapPage />} />
              <Route path="anomalies" element={<AnomaliesPage />} />
              <Route path="datasets" element={<DatasetsPage />} />
              <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
              <Route path="uploads" element={<UploadPage />} />
              <Route path="ingestion" element={<IngestionJobsPage />} />
              <Route path="processing" element={<ProcessingMonitorPage />} />
              <Route
                path="big-data"
                element={
                  <RequireRole roles={['administrator', 'climate_analyst']}>
                    <BigDataPage />
                  </RequireRole>
                }
              />
              <Route
                path="ml-lab"
                element={
                  <RequireRole roles={['administrator', 'climate_analyst']}>
                    <MlLabPage />
                  </RequireRole>
                }
              />
              <Route path="models" element={<ModelRegistryPage />} />
              <Route path="forecasts" element={<ForecastExplorerPage />} />
              <Route path="alerts" element={<AlertsPage />} />
              <Route path="notifications" element={<NotificationsPage />} />
              <Route path="reports" element={<ReportsPage />} />
              <Route path="profile" element={<ProfilePage />} />
              <Route path="support" element={<SupportPage />} />
            </Route>

            <Route
              path="/admin"
              element={
                <RequireAuth>
                  <RequireRole roles={['administrator']}>
                    <AppLayout />
                  </RequireRole>
                </RequireAuth>
              }
            >
              <Route index element={<AdminOverviewPage />} />
              <Route path="users" element={<UsersPage />} />
              <Route path="roles" element={<RolesPage />} />
              <Route path="datasets" element={<DatasetAdminPage />} />
              <Route path="pipelines" element={<PipelineAdminPage />} />
              <Route path="alert-rules" element={<AlertRulesPage />} />
              <Route path="health" element={<SystemHealthPage />} />
              <Route path="tickets" element={<TicketAdminPage />} />
            </Route>

            <Route path="/dashboard" element={<Navigate to="/app" replace />} />
            <Route path="*" element={<PublicLayout />}>
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}
