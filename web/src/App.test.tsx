import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("identifies the product while map work is pending", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Event Discovery" })).toBeInTheDocument();
    expect(screen.getByText("Philadelphia events are coming in Phase 1.")).toBeVisible();
  });
});
