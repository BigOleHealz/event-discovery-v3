import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EventFilterSidebar } from "./EventFilterSidebar";

afterEach(cleanup);

it("shows ancestry and sends a selected parent without expanding children in the browser", () => {
  const change = vi.fn();
  render(<EventFilterSidebar availableCategories={[]} hierarchy={[
    { id: "music", name: "Music", parent_id: null, root_id: "music", aliases: ["music"] },
    { id: "jazz", name: "Jazz", parent_id: "music", root_id: "music", aliases: ["jazz"] },
    { id: "bebop", name: "Bebop", parent_id: "jazz", root_id: "music", aliases: ["bebop"] },
  ]} filters={{ categories: [], startsAfter: null, startsBefore: null,
    timeOfDayStart: "", timeOfDayEnd: "" }} onChange={change} />);
  expect(screen.getByRole("option", { name: "Music / Jazz / Bebop" })).toBeVisible();
  const parent = screen.getByRole("option", { name: "Music" }) as HTMLOptionElement;
  parent.selected = true;
  fireEvent.change(screen.getByRole("listbox", { name: "Categories" }));
  expect(change).toHaveBeenCalledWith(expect.objectContaining({ categories: ["music"] }));
  expect(screen.getByRole("list", { name: "Category color legend" }).children).toHaveLength(1);
});
