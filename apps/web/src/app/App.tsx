import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrandLoading } from "../components/BrandLoader";
import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { Button } from "../components/ui/button";
import { EmptyState } from "../components/ui/empty-state";
import { MotionProvider } from "../components/ui/motion";
import { Toaster } from "../components/ui/sonner";
import { TooltipProvider } from "../components/ui/tooltip";
import { ThemeProvider } from "../design/theme";
import { HomePage } from "../features/home/HomePage";
import { AppShell } from "./AppShell";
import { AdminLayout } from "./shell/AdminNav";

// Route-level code splitting (US-134): Home is in the entry chunk; the Viewer's Ask and dashboard pages, the Creator's
// report and dashboard builders and the Admin screens each load on first visit, so a Viewer never downloads canvas or
// admin code.
const loadAsk = () => import("../features/ask/AskPage");
const loadDashboard = () => import("../features/viewer/DashboardPage");
const AskPage = lazy(() => loadAsk().then((m) => ({ default: m.AskPage })));
const DashboardPage = lazy(() => loadDashboard().then((m) => ({ default: m.DashboardPage })));
const ReportsPage = lazy(() => import("../features/reports/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const ReportPlannerPage = lazy(() => import("../features/reports/ReportPlannerPage").then((m) => ({ default: m.ReportPlannerPage })));
const ReportCanvasPage = lazy(() => import("../features/reports/ReportCanvasPage").then((m) => ({ default: m.ReportCanvasPage })));
const DashboardsPage = lazy(() => import("../features/dashboards/DashboardsPage").then((m) => ({ default: m.DashboardsPage })));
const NewDashboardPage = lazy(() => import("../features/dashboards/DashboardsPage").then((m) => ({ default: m.NewDashboardPage })));
const AiDashboardPage = lazy(() => import("../features/dashboards/AiDashboardPage").then((m) => ({ default: m.AiDashboardPage })));
const ComposerPage = lazy(() => import("../features/dashboards/ComposerPage").then((m) => ({ default: m.ComposerPage })));
const PublishPage = lazy(() => import("../features/dashboards/PublishPage").then((m) => ({ default: m.PublishPage })));
const VersionsPage = lazy(() => import("../features/dashboards/VersionsPage").then((m) => ({ default: m.VersionsPage })));
const FieldsPage = lazy(() => import("../features/fields/FieldsPage").then((m) => ({ default: m.FieldsPage })));
const FieldBuilderPage = lazy(() => import("../features/fields/FieldBuilderPage").then((m) => ({ default: m.FieldBuilderPage })));
const CustomSqlListPage = lazy(() => import("../features/sql/CustomSqlPage").then((m) => ({ default: m.CustomSqlListPage })));
const CustomSqlPage = lazy(() => import("../features/sql/CustomSqlPage").then((m) => ({ default: m.CustomSqlPage })));
const CertificationPage = lazy(() => import("../features/govern/CertificationPage").then((m) => ({ default: m.CertificationPage })));
const PublishRequestsPage = lazy(() => import("../features/govern/PublishRequestsPage").then((m) => ({ default: m.PublishRequestsPage })));
const NotificationsPage = lazy(() => import("../features/notifications/NotificationsPage").then((m) => ({ default: m.NotificationsPage })));
const KnowledgePage = lazy(() => import("../features/admin/knowledge/KnowledgePage").then((m) => ({ default: m.KnowledgePage })));
const GoldExamplesPage = lazy(() => import("../features/admin/knowledge/GoldExamplesPage").then((m) => ({ default: m.GoldExamplesPage })));
const AnswerFeedbackPage = lazy(() => import("../features/admin/knowledge/AnswerFeedbackPage").then((m) => ({ default: m.AnswerFeedbackPage })));
const CurationPage = lazy(() => import("../features/admin/knowledge/CurationPage").then((m) => ({ default: m.CurationPage })));
const ConditionsPage = lazy(() => import("../features/admin/knowledge/ConditionsPage").then((m) => ({ default: m.ConditionsPage })));
const DefinitionsPage = lazy(() => import("../features/admin/knowledge/DefinitionsPage").then((m) => ({ default: m.DefinitionsPage })));
const ValueSynonymsPage = lazy(() => import("../features/admin/knowledge/ValueSynonymsPage").then((m) => ({ default: m.ValueSynonymsPage })));
const PacksPage = lazy(() => import("../features/admin/packs/PacksPage").then((m) => ({ default: m.PacksPage })));
const PackPage = lazy(() => import("../features/admin/packs/PackPage").then((m) => ({ default: m.PackPage })));
const StarterContentPage = lazy(() => import("../features/admin/starter/StarterContentPage").then((m) => ({ default: m.StarterContentPage })));
const BrandingPage = lazy(() => import("../features/admin/branding/BrandingPage").then((m) => ({ default: m.BrandingPage })));
const AiSettingsPage = lazy(() => import("../features/admin/ai/AiSettingsPage").then((m) => ({ default: m.AiSettingsPage })));
const AgentRunsPage = lazy(() => import("../features/admin/ai/AgentRunsPage").then((m) => ({ default: m.AgentRunsPage })));
const AgentsPage = lazy(() => import("../features/admin/ai/AgentsPage").then((m) => ({ default: m.AgentsPage })));
const AgentPage = lazy(() => import("../features/admin/ai/AgentsPage").then((m) => ({ default: m.AgentPage })));
const AuditPage = lazy(() => import("../features/admin/audit/AuditPage").then((m) => ({ default: m.AuditPage })));
const AccessPage = lazy(() => import("../features/admin/access/AccessPage").then((m) => ({ default: m.AccessPage })));
const ConnectionsPage = lazy(() => import("../features/admin/connections/ConnectionsPage").then((m) => ({ default: m.ConnectionsPage })));
const OverviewPage = lazy(() => import("../features/admin/overview/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const SshKeysPage = lazy(() => import("../features/admin/connections/SshKeysPage").then((m) => ({ default: m.SshKeysPage })));
const AddConnectionPage = lazy(() => import("../features/admin/connections/AddConnectionPage").then((m) => ({ default: m.AddConnectionPage })));
const ApproveModelPage = lazy(() => import("../features/admin/model/ApproveModelPage").then((m) => ({ default: m.ApproveModelPage })));
const ModelStudioPage = lazy(() => import("../features/admin/model/ModelStudioPage").then((m) => ({ default: m.ModelStudioPage })));
// The component catalogue exists in dev builds only; the import is dropped from production bundles.
const UiCatalogue = import.meta.env.DEV
  ? lazy(() => import("../features/dev/UiCatalogue").then((m) => ({ default: m.UiCatalogue })))
  : null;

/**
 * Starts loading a Viewer route's code while sign-in is still running (US-148), so opening a dashboard link does not
 * wait for sign-in and then for its chunk in turn. Only the Viewer's read routes are preloaded.
 */
export function preloadRoute(path: string | null) {
  if (!path) return;
  if (/^\/dashboards\/[^/]+\/?$/.test(path) && !path.startsWith("/dashboards/new") && !path.startsWith("/dashboards/ai")) void loadDashboard();
  else if (path.startsWith("/ask")) void loadAsk();
}

function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<BrandLoading />}>{children}</Suspense>;
}

export function AppRoutes() {
  return (
    <Routes>
      {UiCatalogue && <Route path="dev/ui" element={<Page><UiCatalogue /></Page>} />}
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="ask" element={<Page><AskPage /></Page>} />
        <Route path="reports" element={<Page><ReportsPage /></Page>} />
        <Route path="reports/new" element={<Page><ReportPlannerPage /></Page>} />
        <Route path="reports/:id" element={<Page><ReportCanvasPage /></Page>} />
        <Route path="shared" element={<Page><DashboardsPage shared /></Page>} />
        <Route path="dashboards" element={<Page><DashboardsPage /></Page>} />
        <Route path="dashboards/new" element={<Page><NewDashboardPage /></Page>} />
        <Route path="dashboards/ai" element={<Page><AiDashboardPage /></Page>} />
        <Route path="dashboards/:id" element={<Page><DashboardPage /></Page>} />
        <Route path="dashboards/:id/draft" element={<Page><DashboardPage draft /></Page>} />
        <Route path="dashboards/:id/edit" element={<Page><ComposerPage /></Page>} />
        <Route path="dashboards/:id/publish" element={<Page><PublishPage /></Page>} />
        <Route path="dashboards/:id/versions" element={<Page><VersionsPage /></Page>} />
        <Route path="fields" element={<Page><FieldsPage /></Page>} />
        <Route path="fields/new" element={<Page><FieldBuilderPage /></Page>} />
        <Route path="fields/:id" element={<Page><FieldBuilderPage /></Page>} />
        <Route path="sql" element={<Page><CustomSqlListPage /></Page>} />
        <Route path="sql/new" element={<Page><CustomSqlPage /></Page>} />
        <Route path="sql/:id" element={<Page><CustomSqlPage /></Page>} />
        <Route path="notifications" element={<Page><NotificationsPage /></Page>} />
        {/* Admin and Govern screens share the Admin sub-navigation (US-143). */}
        <Route element={<AdminLayout />}>
          <Route path="admin" element={<Page><OverviewPage /></Page>} />
          <Route path="govern/certification" element={<Page><CertificationPage /></Page>} />
          <Route path="govern/publish-requests" element={<Page><PublishRequestsPage /></Page>} />
          <Route path="admin/audit" element={<Page><AuditPage /></Page>} />
          <Route path="admin/ai" element={<Page><AiSettingsPage /></Page>} />
          <Route path="admin/agent-runs" element={<Page><AgentRunsPage /></Page>} />
          <Route path="admin/agents" element={<Page><AgentsPage /></Page>} />
          <Route path="admin/agents/:code" element={<Page><AgentPage /></Page>} />
          <Route path="admin/branding" element={<Page><BrandingPage /></Page>} />
          <Route path="admin/starter" element={<Page><StarterContentPage /></Page>} />
          <Route path="admin/packs" element={<Page><PacksPage /></Page>} />
          <Route path="admin/packs/:id" element={<Page><PackPage /></Page>} />
          <Route path="admin/knowledge" element={<Page><KnowledgePage /></Page>} />
          <Route path="admin/knowledge/gold" element={<Page><GoldExamplesPage /></Page>} />
          <Route path="admin/knowledge/values" element={<Page><ValueSynonymsPage /></Page>} />
          <Route path="admin/knowledge/feedback" element={<Page><AnswerFeedbackPage /></Page>} />
          <Route path="admin/knowledge/curation" element={<Page><CurationPage /></Page>} />
          <Route path="admin/knowledge/definitions" element={<Page><DefinitionsPage /></Page>} />
          <Route path="admin/knowledge/conditions" element={<Page><ConditionsPage /></Page>} />
          <Route path="admin/access/*" element={<Page><AccessPage /></Page>} />
          <Route path="admin/connections" element={<Page><ConnectionsPage /></Page>} />
          <Route path="admin/connections/new" element={<Page><AddConnectionPage /></Page>} />
          <Route path="admin/ssh-keys" element={<Page><SshKeysPage /></Page>} />
          <Route path="admin/models/:id" element={<Page><ModelStudioPage /></Page>} />
          <Route path="admin/models/:id/approve" element={<Page><ApproveModelPage /></Page>} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function NotFound() {
  return (
    <div className="tl grid gap-4">
      <h1 className="sr-only">Page not found</h1>
      <EmptyState title="Page not found" action={<Button asChild variant="secondary"><Link to="/">Go to Home</Link></Button>}>
        The link may be out of date, or the page was moved.
      </EmptyState>
    </div>
  );
}

export function Providers({ children, client }: { children: ReactNode; client?: QueryClient }) {
  const qc = client ?? new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });
  return (
    <QueryClientProvider client={qc}>
      <ThemeProvider>
        <MotionProvider>
          <TooltipProvider>
            <BrowserRouter>{children}</BrowserRouter>
            <Toaster />
          </TooltipProvider>
        </MotionProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
