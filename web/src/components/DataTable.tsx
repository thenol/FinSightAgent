import type { ReactNode } from "react";

export function DataTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: ReactNode[];
}) {
  if (!rows.length) return null;
  return (
    <div className="table-panel">
      <table className="data-table">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}
