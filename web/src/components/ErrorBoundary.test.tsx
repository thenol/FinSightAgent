import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function Broken(): ReactElement {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("renders a recovery panel when a child throws", () => {
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("页面渲染失败");
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
