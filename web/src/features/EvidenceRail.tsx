import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import type { Evidence } from "@/types/api";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";

function HighlightedBody({ content, excerpt }: { content: string; excerpt: string }) {
  if (!content) return <span className="muted">无正文</span>;
  if (!excerpt || !content.includes(excerpt)) return <>{content}</>;
  const parts = content.split(excerpt);
  return (
    <>
      {parts.map((part, index) => (
        <span key={`${index}-${part.slice(0, 8)}`}>
          {part}
          {index < parts.length - 1 ? <span className="highlight">{excerpt}</span> : null}
        </span>
      ))}
    </>
  );
}

export function EvidenceRail({ evidenceIds }: { evidenceIds: string[] }) {
  const query = useQuery({
    queryKey: ["evidence", evidenceIds],
    enabled: evidenceIds.length > 0,
    queryFn: async () =>
      Promise.all(
        evidenceIds.map(async (id) => {
          try {
            return { ok: true as const, value: await apiGet<Evidence>(`/api/v1/evidence/${encodeURIComponent(id)}`) };
          } catch (error) {
            return {
              ok: false as const,
              id,
              error: error instanceof Error ? error.message : String(error),
            };
          }
        }),
      ),
  });

  if (!evidenceIds.length) return <EmptyState>无关联证据</EmptyState>;
  if (query.isLoading) return <Skeleton />;
  if (query.isError) return <ErrorState>证据加载失败</ErrorState>;

  return (
    <div className="evidence-rail">
      {(query.data || []).map((item) => {
        if (!item.ok) {
          return (
            <article key={item.id} className="evidence-card">
              <ErrorState>{`证据 ${item.id}：${item.error}`}</ErrorState>
            </article>
          );
        }
        const evidence = item.value;
        return (
          <article key={evidence.id} className="evidence-card">
            <h4>证据 {evidence.id}</h4>
            <p className="muted">
              {evidence.document_title || "无标题"} · {evidence.document_url || evidence.document_id}
            </p>
            <p className="muted mono">
              {evidence.locator_type} / {evidence.extraction_version} ·{" "}
              {JSON.stringify(evidence.locator)}
            </p>
            <h5>摘录</h5>
            <pre className="pre">{evidence.excerpt}</pre>
            <h5>来源正文</h5>
            <pre className="pre">
              <HighlightedBody content={evidence.document_content || ""} excerpt={evidence.excerpt || ""} />
            </pre>
          </article>
        );
      })}
    </div>
  );
}
