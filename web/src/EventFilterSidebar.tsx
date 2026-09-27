import type { ChangeEvent } from "react";
import { categoryOptions, categoryRoots, type Category } from "./categories";

import { pinStyleForCategory } from "./categoryPinStyle";
import { dateInputValue, endOfUtcDate, startOfUtcDate } from "./eventFilterState";
import type { EventFilters } from "./events";

interface EventFilterSidebarProps {
  availableCategories: string[];
  hierarchy?: Category[];
  categoryError?: string | null;
  filters: EventFilters;
  onChange: (filters: EventFilters) => void;
}

const EMPTY_FILTERS: EventFilters = {
  startsAfter: null,
  startsBefore: null,
  timeOfDayStart: "",
  timeOfDayEnd: "",
  categories: [],
};

export function EventFilterSidebar({
  availableCategories,
  hierarchy = [],
  categoryError = null,
  filters,
  onChange,
}: EventFilterSidebarProps) {
  const roots = categoryRoots(hierarchy);
  const options = categoryOptions(hierarchy);
  const ids = new Set(options.map((option) => option.id));
  for (const category of new Set([...availableCategories, ...filters.categories])) {
    if (ids.has(category)) continue;
    const root = roots.get(category.trim().toLowerCase());
    if (root !== undefined && !filters.categories.includes(category)) continue;
    options.push({ id: category, name: category, path: category, rootId: root ?? category, depth: 0 });
  }
  const legend = hierarchy.length > 0
    ? options.filter((option) => option.depth === 0 && ids.has(option.id)) : options;
  function updateCategories(event: ChangeEvent<HTMLSelectElement>): void {
    onChange({
      ...filters,
      categories: Array.from(event.currentTarget.selectedOptions, (option) => option.value),
    });
  }

  return (
    <aside className="filter-sidebar" aria-label="Event filters">
      <div className="filter-heading">
        <div>
          <p className="eyebrow">Narrow the map</p>
          <h2>Filters</h2>
        </div>
        <button className="filter-clear" type="button" onClick={() => onChange(EMPTY_FILTERS)}>
          Clear
        </button>
      </div>

      <fieldset className="filter-group">
        <legend>Date range</legend>
        <div className="filter-pair">
          <label>
            From
            <input
              type="date"
              value={dateInputValue(filters.startsAfter)}
              max={dateInputValue(filters.startsBefore) || undefined}
              onChange={(event) =>
                onChange({ ...filters, startsAfter: startOfUtcDate(event.currentTarget.value) })
              }
            />
          </label>
          <label>
            Through
            <input
              type="date"
              value={dateInputValue(filters.startsBefore)}
              min={dateInputValue(filters.startsAfter) || undefined}
              onChange={(event) =>
                onChange({ ...filters, startsBefore: endOfUtcDate(event.currentTarget.value) })
              }
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="filter-group">
        <legend>Time of day</legend>
        <div className="filter-pair">
          <label>
            From
            <input
              type="time"
              value={filters.timeOfDayStart}
              onChange={(event) =>
                onChange({ ...filters, timeOfDayStart: event.currentTarget.value })
              }
            />
          </label>
          <label>
            Through
            <input
              type="time"
              value={filters.timeOfDayEnd}
              onChange={(event) =>
                onChange({ ...filters, timeOfDayEnd: event.currentTarget.value })
              }
            />
          </label>
        </div>
        <p className="filter-hint">Overnight ranges, such as 10 PM–2 AM, are supported.</p>
      </fieldset>

      <div className="filter-group filter-category">
        <label htmlFor="event-category-filter">Categories</label>
        <select
          id="event-category-filter"
          multiple
          value={filters.categories}
          size={Math.min(Math.max(options.length, 3), 10)}
          aria-describedby="event-category-hint"
          onChange={updateCategories}
        >
          {options.map((category) => (
            <option key={category.id} value={category.id} aria-label={category.path}>
              {`${"\u00a0\u00a0".repeat(category.depth)}${category.depth > 0 ? "↳ " : ""}${category.name}`}
            </option>
          ))}
        </select>
        <span id="event-category-hint" className="filter-hint">
          Choose one or more. Parent categories include all subcategories. Subcategories share their parent’s pin color.
        </span>
        {categoryError !== null ? <p aria-live="polite" className="filter-hint">{categoryError}</p> : null}
        {legend.length > 0 ? (
          <ul className="category-legend" aria-label="Category color legend">
            {legend.map((category) => (
              <li key={category.id}>
                <span
                  className="category-swatch"
                  style={{ backgroundColor: pinStyleForCategory(category.id, category.rootId).background }}
                  aria-hidden="true"
                />
                <span>{category.name}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </aside>
  );
}
