export interface Category {
  id: string;
  name: string;
  parent_id: string | null;
  root_id: string;
  aliases: string[];
}

export function isCategoryList(value: unknown): value is Category[] {
  return Array.isArray(value) && value.every((item: unknown) => {
    if (typeof item !== "object" || item === null) return false;
    const row = item as Record<string, unknown>;
    return typeof row.id === "string" && typeof row.name === "string" &&
      (row.parent_id === null || typeof row.parent_id === "string") &&
      typeof row.root_id === "string" && Array.isArray(row.aliases) &&
      row.aliases.every((alias: unknown) => typeof alias === "string");
  });
}

export async function fetchCategories(apiBaseUrl: string, signal: AbortSignal): Promise<Category[]> {
  const url = `${apiBaseUrl.replace(/\/$/, "")}/api/categories`;
  const cacheKey = `event-discovery-categories-v1:${new URL(url, window.location.href).href}`;
  try {
    const response = await fetch(url, { signal, credentials: "omit" });
    if (!response.ok) throw new Error("Category filters are temporarily unavailable.");
    const payload: unknown = await response.json();
    if (!isCategoryList(payload)) throw new Error("Invalid category response.");
    try { localStorage.setItem(cacheKey, JSON.stringify(payload)); } catch { /* best effort */ }
    return payload;
  } catch (error: unknown) {
    signal.throwIfAborted();
    try {
      const cached: unknown = JSON.parse(localStorage.getItem(cacheKey) ?? "null");
      if (isCategoryList(cached)) return cached;
    } catch { /* storage may be unavailable */ }
    throw error;
  }
}

export function categoryRoots(categories: Category[]): Map<string, string> {
  const roots = new Map<string, string>();
  for (const category of categories) {
    for (const label of [category.id, ...category.aliases]) roots.set(label, category.root_id);
  }
  return roots;
}

export interface CategoryOption {
  id: string;
  name: string;
  path: string;
  rootId: string;
  depth: number;
}

export function categoryOptions(categories: Category[]): CategoryOption[] {
  const children = new Map<string | null, Category[]>();
  for (const category of categories) {
    const siblings = children.get(category.parent_id) ?? [];
    siblings.push(category);
    children.set(category.parent_id, siblings);
  }
  const result: CategoryOption[] = [];
  const seen = new Set<string>();
  function visit(parent: string | null, names: string[]): void {
    for (const category of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      if (seen.has(category.id)) continue;
      seen.add(category.id);
      const path = [...names, category.name];
      result.push({ id: category.id, name: category.name, path: path.join(" / "),
        rootId: category.root_id, depth: names.length });
      visit(category.id, path);
    }
  }
  visit(null, []);
  return result;
}
