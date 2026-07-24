export function EmptyState({ children }: { children: string }) {
  return <div className="empty-state">{children}</div>;
}

export function ErrorState({ children }: { children: string }) {
  return (
    <div className="error-state" role="alert">
      {children}
    </div>
  );
}

export function Skeleton() {
  return <div className="skeleton-block" aria-hidden="true" />;
}
