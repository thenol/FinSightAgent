import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/app/AuthContext";
import type { Role } from "@/types/api";

type RequireRoleProps = {
  allow: (role: Role | null) => boolean;
};

export function RequireRole({ allow }: RequireRoleProps) {
  const { role } = useAuth();
  const location = useLocation();
  if (!allow(role)) {
    return <Navigate to="/" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
