import { describe, expect, it } from "vitest";

import {
  AGGREGATED_CELL_PIN_STYLE,
  UNCATEGORIZED_PIN_STYLE,
  pinStyleForCategory,
} from "./categoryPinStyle";

describe("pinStyleForCategory", () => {
  it("keeps a category color stable across casing and surrounding whitespace", () => {
    expect(pinStyleForCategory("music")).toBe(pinStyleForCategory(" Music "));
  });

  it("assigns different palette colors to the representative map categories", () => {
    expect(pinStyleForCategory("music")).not.toBe(pinStyleForCategory("science"));
  });

  it("caps category colors at eight", () => {
    const colors = new Set(
      Array.from({ length: 100 }, (_, index) => pinStyleForCategory(`category-${index}`).background),
    );

    expect(colors.size).toBe(8);
  });

  it("keeps uncategorized pins and aggregated cells neutral", () => {
    expect(pinStyleForCategory(null)).toBe(UNCATEGORIZED_PIN_STYLE);
    expect(pinStyleForCategory("  ")).toBe(UNCATEGORIZED_PIN_STYLE);
    expect(AGGREGATED_CELL_PIN_STYLE).toEqual({
      background: "#59636e",
      borderColor: "#303841",
      glyphColor: "#fffaf0",
    });
  });
});
