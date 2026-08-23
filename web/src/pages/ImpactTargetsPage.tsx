import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { apiGet } from "@/lib/api";
import type { ImpactPortfolioTarget, ImpactSnapshot } from "@/types/api";

export function ImpactTargetsPage() {
  const targetsQuery = useQuery({
    queryKey: ["impact-targets"],
    queryFn: () => apiGet<ImpactPortfolioTarget[]>("/api/v1/impact-targets"),
  });
  const targets = targetsQuery.data || [];

  return (
    <>
      <PageHeader
        eyebrow="Impact portfolio"
        title="目标影响"
        description="跨已批准事件聚合的行业、公司、市场和资产影响；每条结论均可回溯到事件贡献。"
      />
      {targetsQuery.isLoading ? <Skeleton /> : null}
      {targetsQuery.isError ? <ErrorState>目标影响加载失败</ErrorState> : null}
      {!targetsQuery.isLoading && !targets.length ? (
        <EmptyState>尚无已批准分析可聚合</EmptyState>
      ) : null}
      {targets.length ? (
        <DataTable
          headers={["目标", "类型", "综合方向", "净影响", "正/负总强度", "置信度", "更新时间"]}
          rows={targets.map((target) => (
            <TargetRow key={target.id} target={target} />
          ))}
        />
      ) : null}
    </>
  );
}

function TargetRow({ target }: { target: ImpactPortfolioTarget }) {
  const snapshotQuery = useQuery({
    queryKey: ["impact-target-snapshot", target.id],
    queryFn: () =>
      apiGet<ImpactSnapshot>(
        `/api/v1/impact-targets/${encodeURIComponent(target.id)}/snapshot`,
      ),
  });
  const snapshot = snapshotQuery.data;
  return (
    <tr>
      <td>
        <Link to={`/impact-targets/${encodeURIComponent(target.id)}`}>
          {target.canonical_name}
        </Link>
        <div className="muted mono">{target.target_code}</div>
      </td>
      <td>{target.target_type}</td>
      <td>{snapshot ? <StatusBadge value={snapshot.direction} /> : "–"}</td>
      <td className="mono">{snapshot ? snapshot.net_score.toFixed(2) : "–"}</td>
      <td className="mono">
        {snapshot ? `${snapshot.positive_gross.toFixed(2)} / ${snapshot.negative_gross.toFixed(2)}` : "–"}
      </td>
      <td>{snapshot ? `${Math.round(snapshot.confidence * 100)}%` : "–"}</td>
      <td>{snapshot ? new Date(snapshot.as_of).toLocaleString("zh-CN") : "–"}</td>
    </tr>
  );
}
