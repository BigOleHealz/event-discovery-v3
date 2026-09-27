import { afterEach, describe, expect, it, vi } from "vitest";
import { categoryOptions, categoryRoots, fetchCategories, type Category } from "./categories";
import { pinStyleForCategory } from "./categoryPinStyle";

export const hierarchy: Category[] = [
  { id: "bebop", name: "Bebop", parent_id: "jazz", root_id: "music", aliases: ["bebop"] },
  { id: "music", name: "Music", parent_id: null, root_id: "music", aliases: ["music"] },
  { id: "jazz", name: "Jazz", parent_id: "music", root_id: "music", aliases: ["jazz"] },
];

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

describe("category hierarchy", () => {
  it("orders arbitrary-depth children and inherits one root color", () => {
    expect(categoryOptions(hierarchy).map(({ id, path, depth }) => ({ id, path, depth }))).toEqual([
      { id: "music", path: "Music", depth: 0 },
      { id: "jazz", path: "Music / Jazz", depth: 1 },
      { id: "bebop", path: "Music / Jazz / Bebop", depth: 2 },
    ]);
    const roots = categoryRoots(hierarchy);
    expect(pinStyleForCategory("bebop", roots.get("bebop"))).toBe(pinStyleForCategory("music"));
    expect(pinStyleForCategory("jazz", roots.get("jazz"))).toBe(pinStyleForCategory("music"));
  });

  it("uses cached ancestry offline and keeps API origins separate", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(hierarchy)));
    vi.stubGlobal("fetch", fetcher);
    expect(await fetchCategories(".", new AbortController().signal)).toEqual(hierarchy);
    fetcher.mockRejectedValue(new TypeError("Offline"));
    expect(await fetchCategories(".", new AbortController().signal)).toEqual(hierarchy);
    await expect(fetchCategories("https://other.test", new AbortController().signal)).rejects.toThrow();
  });
});
