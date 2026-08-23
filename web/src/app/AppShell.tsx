import { useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/app/AuthContext";
import { apiGet } from "@/lib/api";
import { asList } from "@/lib/format";
import type { ReviewTask, Source, Workflow } from "@/types/api";

type NavItem = {
  to: string;
  label: string;
  end?: boolean;
  badge?: "reviews" | "sources" | "workflows";
  icon: ReactNode;
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

const ICONS = {
  overview: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 4h7v7H4V4Zm9 0h7v4h-7V4ZM4 13h7v7H4v-7Zm9-2h7v9h-7v-9Z" />
    </svg>
  ),
  reviews: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 4h14v2H5V4Zm0 5h14v11H5V9Zm3 3v2h8v-2H8Z" />
    </svg>
  ),
  events: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 3v2H5v16h14V5h-2V3h-2v2H9V3H7Zm0 6h10v2H7V9Zm0 4h7v2H7v-2Z" />
    </svg>
  ),
  reports: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3h9l5 5v13H6V3Zm8 1.5V9h4.5L14 4.5ZM8 12h8v2H8v-2Zm0 4h5v2H8v-2Z" />
    </svg>
  ),
  sources: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 6a8 8 0 0 1 16 0v2H4V6Zm0 4h16v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8Zm4 3v2h8v-2H8Z" />
    </svg>
  ),
  documents: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 3h7l5 5v13H7V3Zm6 1.5V9h4.5L13 4.5ZM9 12h6v2H9v-2Zm0 4h4v2H9v-2Z" />
    </svg>
  ),
  models: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2 4 7v10l8 5 8-5V7l-8-5Zm0 2.2 5.5 3.44L12 11.1 6.5 7.64 12 4.2ZM6 9.4l5 3.1v6.3l-5-3.1V9.4Zm7 9.4v-6.3l5-3.1v6.3l-5 3.1Z" />
    </svg>
  ),
  workflows: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 4a3 3 0 1 1-2.83 4H3v3h1.17A3 3 0 1 1 7 17h10a3 3 0 1 1 2.83-4H21V9h-1.17A3 3 0 1 1 17 4H7Zm0 2h10a1 1 0 1 1 0 2H7a1 1 0 1 1 0-2Zm0 9a1 1 0 1 0 0 2h10a1 1 0 1 0 0-2H7Z" />
    </svg>
  ),
  research: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 2a7 7 0 1 0 4.6 12.2l5.1 5.1 1.4-1.4-5.1-5.1A7 7 0 0 0 9 2Zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10Z" />
    </svg>
  ),
  briefs: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3h12v18H6V3Zm2 2v14h8V5H8Zm2 2h4v2h-4V7Zm0 4h4v2h-4v-2Zm0 4h3v2h-3v-2Z" />
    </svg>
  ),
  audit: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Zm0 2.2 6 2.25V11c0 3.9-2.5 7.4-6 8.8-3.5-1.4-6-4.9-6-8.8V6.45l6-2.25Zm-1 4.3v5l4 2 .9-1.5-3.1-1.55V8.5H11Z" />
    </svg>
  ),
  merge: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0 2a1 1 0 1 1 0 2 1 1 0 0 1 0-2Zm8 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0 2a1 1 0 1 1 0 2 1 1 0 0 1 0-2ZM8 11h8v2H8v-2Zm3 3h2v6h-2v-6Zm-3 2h2v4H8v-4Zm6 0h2v4h-2v-4Z" />
    </svg>
  ),
  types: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 5h16v3H4V5Zm0 5h10v3H4v-3Zm0 5h16v3H4v-3Zm12-5h4v3h-4v-3Z" />
    </svg>
  ),
} as const;

const NAV_GROUPS: NavGroup[] = [
  {
    label: "业务工作台",
    items: [
      { to: "/", label: "总览", end: true, icon: ICONS.overview },
      { to: "/reviews", label: "审核中心", badge: "reviews", icon: ICONS.reviews },
      { to: "/merge-reviews", label: "事件合并审核", icon: ICONS.merge },
      { to: "/event-types", label: "事件类型", icon: ICONS.types },
      { to: "/events", label: "事件证据", icon: ICONS.events },
      { to: "/impact-targets", label: "目标影响", icon: ICONS.briefs },
      { to: "/future-events", label: "研究日历", icon: ICONS.briefs },
      { to: "/market-outlook", label: "市场展望", icon: ICONS.briefs },
      { to: "/forecast-evaluation", label: "预测评估", icon: ICONS.models },
      { to: "/market-master-data", label: "市场主数据", icon: ICONS.types },
      { to: "/reports", label: "研究报告", icon: ICONS.reports },
      { to: "/research", label: "动态研究", icon: ICONS.research },
    ],
  },
  {
    label: "运维与合规",
    items: [
      { to: "/sources", label: "来源", badge: "sources", icon: ICONS.sources },
      { to: "/documents", label: "文档保留", icon: ICONS.documents },
      { to: "/models", label: "模型配置", icon: ICONS.models },
      { to: "/workflows", label: "工作流", badge: "workflows", icon: ICONS.workflows },
      { to: "/briefs", label: "每日简报", icon: ICONS.briefs },
      { to: "/audit", label: "审计记录", icon: ICONS.audit },
    ],
  },
];

function isDetailPath(pathname: string, root: string): boolean {
  return pathname === root || pathname.startsWith(`${root}/`);
}

function titleFor(pathname: string): { crumbs: string[]; title: string } {
  if (isDetailPath(pathname, "/reviews")) {
    return pathname === "/reviews"
      ? { crumbs: ["审核中心"], title: "审核中心" }
      : { crumbs: ["审核中心", "任务详情"], title: "审核任务" };
  }
  if (isDetailPath(pathname, "/merge-reviews")) {
    return pathname === "/merge-reviews"
      ? { crumbs: ["事件合并审核"], title: "事件合并审核" }
      : { crumbs: ["事件合并审核", "任务详情"], title: "合并审核任务" };
  }
  if (pathname.startsWith("/event-types")) return { crumbs: ["事件类型"], title: "事件类型词表" };
  if (isDetailPath(pathname, "/events")) {
    return pathname === "/events"
      ? { crumbs: ["事件证据"], title: "事件证据" }
      : { crumbs: ["事件证据", "事件详情"], title: "事件详情" };
  }
  if (isDetailPath(pathname, "/impact-targets")) {
    return pathname === "/impact-targets"
      ? { crumbs: ["目标影响"], title: "目标影响" }
      : pathname.includes("/forward")
        ? { crumbs: ["目标影响", "行业前瞻"], title: "行业前瞻" }
        : { crumbs: ["目标影响", "目标详情"], title: "目标影响详情" };
  }
  if (pathname.startsWith("/future-events")) return { crumbs: ["研究日历"], title: "未来事件研究日历" };
  if (pathname.startsWith("/market-outlook")) return { crumbs: ["市场展望"], title: "市场展望" };
  if (pathname.startsWith("/forecast-evaluation")) return { crumbs: ["预测评估"], title: "预测评估与校准" };
  if (pathname.startsWith("/market-master-data")) return { crumbs: ["市场主数据"], title: "市场主数据治理" };
  if (isDetailPath(pathname, "/reports")) {
    return pathname === "/reports"
      ? { crumbs: ["研究报告"], title: "研究报告" }
      : { crumbs: ["研究报告", "报告详情"], title: "报告详情" };
  }
  if (pathname.startsWith("/sources")) return { crumbs: ["来源"], title: "来源运维" };
  if (pathname.startsWith("/documents")) return { crumbs: ["文档保留"], title: "文档保留" };
  if (pathname.startsWith("/models")) return { crumbs: ["模型配置"], title: "模型配置" };
  if (isDetailPath(pathname, "/workflows")) {
    return pathname === "/workflows"
      ? { crumbs: ["工作流"], title: "研究工作流" }
      : { crumbs: ["工作流", "运行详情"], title: "工作流详情" };
  }
  if (isDetailPath(pathname, "/research")) {
    return pathname === "/research"
      ? { crumbs: ["动态研究"], title: "动态研究" }
      : { crumbs: ["动态研究", "计划详情"], title: "研究计划详情" };
  }
  if (pathname.startsWith("/briefs")) return { crumbs: ["每日简报"], title: "每日简报" };
  if (pathname.startsWith("/audit")) return { crumbs: ["审计记录"], title: "审计记录" };
  return { crumbs: ["总览"], title: "运营总览" };
}

const COLLAPSE_KEY = "finsight.admin.sidebarCollapsed";

export function AppShell() {
  const { username, role, logout } = useAuth();
  const location = useLocation();
  const heading = titleFor(location.pathname);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "1");
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    function onResize() {
      if (window.matchMedia("(min-width: 961px)").matches) {
        setMobileOpen(false);
      }
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const sourcesQuery = useQuery({
    queryKey: ["sources"],
    queryFn: () => apiGet<Source[] | { items: Source[] }>("/api/v1/sources"),
  });
  const reviewsQuery = useQuery({
    queryKey: ["reviews", "pending"],
    queryFn: () =>
      apiGet<ReviewTask[] | { items: ReviewTask[] }>("/api/v1/reviews?status_filter=pending"),
  });
  const workflowsQuery = useQuery({
    queryKey: ["workflows", "active"],
    queryFn: () => apiGet<Workflow[] | { items: Workflow[] }>("/api/v1/workflows?limit=200"),
  });

  const badges = useMemo(() => {
    const sources = asList<Source>(sourcesQuery.data);
    const reviews = asList<ReviewTask>(reviewsQuery.data);
    const workflows = asList<Workflow>(workflowsQuery.data).filter((item) =>
      ["pending", "running", "waiting_review"].includes(item.status),
    );
    return {
      reviews: reviews.length,
      sources: sources.filter((item) => item.status !== "active").length,
      workflows: workflows.length,
    };
  }, [sourcesQuery.data, reviewsQuery.data, workflowsQuery.data]);

  const initials = (username || "OP").slice(0, 2).toUpperCase();

  function badgeValue(kind?: NavItem["badge"]): number {
    if (!kind) return 0;
    return badges[kind];
  }

  function badgeClass(kind?: NavItem["badge"]): string {
    if (kind === "reviews") return "badge warn";
    if (kind === "sources") return "badge bad";
    return "badge";
  }

  return (
    <div className={`app-shell${collapsed ? " is-collapsed" : ""}${mobileOpen ? " is-mobile-open" : ""}`}>
      {mobileOpen ? (
        <div
          className="sidebar-backdrop"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      <aside className="sidebar" aria-label="管理导航">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            FS
          </div>
          <div className="brand-copy">
            <strong>FinSight</strong>
            <span>管理后台</span>
          </div>
          <button
            type="button"
            className="icon-button sidebar-collapse"
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            onClick={() => setCollapsed((value) => !value)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 6h12v2H8V6Zm0 5h12v2H8v-2Zm0 5h12v2H8v-2ZM4 6h2v2H4V6Zm0 5h2v2H4v-2Zm0 5h2v2H4v-2Z" />
            </svg>
          </button>
        </div>

        <nav className="nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="nav-group">
              <p className="nav-group-label">{group.label}</p>
              {group.items.map((item) => {
                const count = badgeValue(item.badge);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={Boolean(item.end)}
                    title={item.label}
                    className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                  >
                    <span className="nav-icon">{item.icon}</span>
                    <span className="nav-label">{item.label}</span>
                    {count ? <span className={badgeClass(item.badge)}>{count}</span> : null}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip" title={`${username || "operator"} · ${role || "unknown"}`}>
            <span className="user-avatar" aria-hidden="true">
              {initials}
            </span>
            <span className="user-meta">
              <strong>{username || "operator"}</strong>
              <span>{role || "unknown"}</span>
            </span>
          </div>
          <button type="button" className="button ghost sidebar-logout" onClick={logout}>
            退出
          </button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="icon-button mobile-menu"
              aria-label="打开导航"
              onClick={() => setMobileOpen(true)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 6h16v2H4V6Zm0 5h16v2H4v-2Zm0 5h16v2H4v-2Z" />
              </svg>
            </button>
            <div className="breadcrumb" aria-label="面包屑">
              <span>管理后台</span>
              {heading.crumbs.map((crumb) => (
                <span key={crumb}>{crumb}</span>
              ))}
            </div>
          </div>
          <div className="topbar-right">
            <p className="topbar-note">判断基准：报告 as_of</p>
            <div className="topbar-user">
              <span className="user-avatar sm" aria-hidden="true">
                {initials}
              </span>
              <span>
                <strong>{username || "operator"}</strong>
                <span className="muted">{role || "unknown"}</span>
              </span>
              <button type="button" className="button ghost" onClick={logout}>
                退出
              </button>
            </div>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
