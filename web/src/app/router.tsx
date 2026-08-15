import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "@/app/AuthContext";
import { AppShell } from "@/app/AppShell";
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
          <Route path="reviews" element={<ReviewsPage />} />
          <Route path="reviews/:taskId" element={<ReviewsPage />} />
          <Route path="merge-reviews" element={<MergeReviewsPage />} />
          <Route path="merge-reviews/:taskId" element={<MergeReviewsPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="events/:eventId" element={<EventsPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="reports/:reportId" element={<ReportsPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="workflows" element={<WorkflowsPage />} />
          <Route path="workflows/:workflowId" element={<WorkflowsPage />} />
          <Route path="research" element={<ResearchPage />} />
          <Route path="research/:planId" element={<ResearchPage />} />
          <Route path="briefs" element={<BriefsPage />} />
          <Route path="audit" element={<AuditPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
