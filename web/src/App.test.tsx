import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("explains which required browser configuration is missing", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Event Discovery" })).toBeInTheDocument();
    expect(screen.getByText("VITE_API_BASE_URL is required")).toBeVisible();
  });
});
