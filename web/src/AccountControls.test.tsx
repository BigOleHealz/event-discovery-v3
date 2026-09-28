import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountControls } from "./AccountControls";

const user = { id: "user-one", display_name: "Test Friend", email: "friend@example.test" };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("account controls", () => {
  it("offers Google sign-in to visitors", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    render(<AccountControls apiBaseUrl="https://api.example.test/" />);
    expect(await screen.findByRole("link", { name: "Sign in with Google" })).toHaveAttribute(
      "href", "https://api.example.test/api/auth/google/start",
    );
    expect(fetch).toHaveBeenCalledWith("https://api.example.test/api/me", expect.objectContaining({
      credentials: "include", cache: "no-store",
    }));
  });

  it("loads the current user and signs out through the API", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(user))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    render(<AccountControls apiBaseUrl="https://api.example.test" />);
    expect(await screen.findByText("Signed in as Test Friend")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("link", { name: "Sign in with Google" })).toBeVisible();
    expect(fetch).toHaveBeenLastCalledWith("https://api.example.test/api/auth/logout", {
      method: "POST", credentials: "include", cache: "no-store",
    });
  });

  it("preserves signed-in state when logout fails", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(user))
      .mockRejectedValueOnce(new TypeError("offline"));
    vi.stubGlobal("fetch", fetch);
    render(<AccountControls apiBaseUrl="https://api.example.test" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("Unable to sign out. Please try again online.")).toBeVisible();
    expect(screen.getByText("Signed in as Test Friend")).toBeVisible();
  });

  it("asks for sign-in when refreshing an expired session", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json(user))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    render(<AccountControls apiBaseUrl="https://api.example.test" />);
    await screen.findByText("Signed in as Test Friend");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(await screen.findByText("Your session expired. Sign in again.")).toBeVisible();
    expect(fetch).toHaveBeenLastCalledWith("https://api.example.test/api/auth/refresh",
      expect.objectContaining({ method: "POST", credentials: "include", cache: "no-store" }));
  });

  it("explains a cancelled callback", async () => {
    window.history.replaceState({}, "", "/?auth_error=1");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    render(<AccountControls apiBaseUrl="https://api.example.test" />);
    expect(screen.getByText("Sign-in did not finish. Please try again.")).toBeVisible();
    expect(await screen.findByRole("link", { name: "Sign in with Google" })).toBeVisible();
  });
});
