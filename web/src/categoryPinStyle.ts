export interface CategoryPinStyle {
  background: string;
  borderColor: string;
  glyphColor: string;
}

// Eight colors is the practical limit for reliably distinguishing categories on a 24px pin.
// This palette is based on the colorblind-safe Okabe-Ito palette, with darker borders so
// each marker remains legible against both light and dark map tiles.
const CATEGORY_PIN_PALETTE: readonly CategoryPinStyle[] = [
  { background: "#0072b2", borderColor: "#003f63", glyphColor: "#ffffff" },
  { background: "#e69f00", borderColor: "#765200", glyphColor: "#17130a" },
  { background: "#009e73", borderColor: "#005840", glyphColor: "#ffffff" },
  { background: "#d55e00", borderColor: "#733300", glyphColor: "#ffffff" },
  { background: "#cc79a7", borderColor: "#713f5d", glyphColor: "#17130a" },
  { background: "#56b4e9", borderColor: "#236281", glyphColor: "#17130a" },
  { background: "#f0e442", borderColor: "#756e0b", glyphColor: "#17130a" },
  { background: "#4d4d4d", borderColor: "#242424", glyphColor: "#ffffff" },
];

export const UNCATEGORIZED_PIN_STYLE: CategoryPinStyle = {
  background: "#7a817c",
  borderColor: "#3d423f",
  glyphColor: "#ffffff",
};

export const AGGREGATED_CELL_PIN_STYLE: CategoryPinStyle = {
  background: "#59636e",
  borderColor: "#303841",
  glyphColor: "#fffaf0",
};

function normalizedCategory(category: string | null): string {
  return category?.trim().toLowerCase() ?? "";
}

export function pinStyleForCategory(category: string | null): CategoryPinStyle {
  const normalized = normalizedCategory(category);
  if (normalized === "") {
    return UNCATEGORIZED_PIN_STYLE;
  }

  let hash = 2_166_136_261;
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }

  return CATEGORY_PIN_PALETTE[(hash >>> 0) % CATEGORY_PIN_PALETTE.length] ?? UNCATEGORIZED_PIN_STYLE;
}
