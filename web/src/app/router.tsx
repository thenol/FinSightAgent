import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "@/app/AuthContext";
import { AppShell } from "@/app/AppShell";
import { RequireRole } from "@/app/RequireRole";
import {
  canManageDocuments,
  canManageLlm,
  canManageMarketMasterData,
  canReview,
  canRunResearch,
  canRunWorkflow,
  canViewAudit,
  hasBusinessRole,
} from "@/lib/roles";
import { LoginPage } from "@/pages/LoginPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { ReviewsPage } from "@/pages/ReviewsPage";
import { EventsPage } from "@/pages/EventsPage";
import { ReportsPage } from "@/pages/ReportsPage";
import { SourcesPage } from "@/pages/SourcesPage";
import { WorkflowsPage } from "@/pages/WorkflowsPage";
import { BriefsPage } from "@/pages/BriefsPage";
import { ModelsPage } from "@/pages/ModelsPage";
import { DocumentsPage } from "@/pages/DocumentsPage";
import { AuditPage } from "@/pages/AuditPage";
import { MergeReviewsPage } from "@/pages/MergeReviewsPage";
import { ResearchPage } from "@/pages/ResearchPage";
import { EventTypesPage } from "@/pages/EventTypesPage";
import { ImpactTargetsPage } from "@/pages/ImpactTargetsPage";
import { ImpactTargetDetailPage } from "@/pages/ImpactTargetDetailPage";
import { ForwardImpactPage } from "@/pages/ForwardImpactPage";
import { FutureEventsPage } from "@/pages/FutureEventsPage";
import { MarketOutlookPage } from "@/pages/MarketOutlookPage";
import { ForecastEvaluationPage } from "@/pages/ForecastEvaluationPage";
import { MarketMasterDataPage } from "@/pages/MarketMasterDataPage";
import { SystemManagementPage } from "@/pages/SystemManagementPage";

function RequireAuth() {
  const { token } = useAuth();
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route element={<RequireRole allow={canReview} />}>
            <Route path="reviews" element={<ReviewsPage />} />
            <Route path="reviews/:taskId" element={<ReviewsPage />} />
            <Route path="merge-reviews" element={<MergeReviewsPage />} />
            <Route path="merge-reviews/:taskId" element={<MergeReviewsPage />} />
            <Route path="event-types" element={<EventTypesPage />} />
          </Route>
          <Route element={<RequireRole allow={hasBusinessRole} />}>
            <Route path="events" element={<EventsPage />} />
            <Route path="events/:eventId" element={<EventsPage />} />
            <Route path="impact-targets" element={<ImpactTargetsPage />} />
            <Route path="impact-targets/:targetId" element={<ImpactTargetDetailPage />} />
            <Route path="impact-targets/:targetId/forward" element={<ForwardImpactPage />} />
            <Route path="future-events" element={<FutureEventsPage />} />
            <Route path="market-outlook" element={<MarketOutlookPage />} />
            <Route path="forecast-evaluation" element={<ForecastEvaluationPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="reports/:reportId" element={<ReportsPage />} />
            <Route path="briefs" element={<BriefsPage />} />
          </Route>
          <Route element={<RequireRole allow={canManageMarketMasterData} />}>
            <Route path="market-master-data" element={<MarketMasterDataPage />} />
          </Route>
          <Route element={<RequireRole allow={hasBusinessRole} />}>
            <Route path="sources" element={<SourcesPage />} />
          </Route>
          <Route element={<RequireRole allow={canManageDocuments} />}>
            <Route path="documents" element={<DocumentsPage />} />
          </Route>
          <Route element={<RequireRole allow={canManageLlm} />}>
            <Route path="models" element={<ModelsPage />} />
            <Route path="system" element={<SystemManagementPage />} />
          </Route>
          <Route element={<RequireRole allow={canRunWorkflow} />}>
            <Route path="workflows" element={<WorkflowsPage />} />
            <Route path="workflows/:workflowId" element={<WorkflowsPage />} />
          </Route>
          <Route element={<RequireRole allow={canRunResearch} />}>
            <Route path="research" element={<ResearchPage />} />
            <Route path="research/:planId" element={<ResearchPage />} />
          </Route>
          <Route element={<RequireRole allow={canViewAudit} />}>
            <Route path="audit" element={<AuditPage />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
