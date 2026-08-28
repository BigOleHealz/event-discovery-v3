import type { ChangeEvent } from "react";

import { pinStyleForCategory } from "./categoryPinStyle";
import { dateInputValue, endOfUtcDate, startOfUtcDate } from "./eventFilterState";
import type { EventFilters } from "./events";

interface EventFilterSidebarProps {
  availableCategories: string[];
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
  filters,
  onChange,
}: EventFilterSidebarProps) {
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
          size={Math.min(Math.max(availableCategories.length, 3), 6)}
          aria-describedby="event-category-hint"
          onChange={updateCategories}
        >
          {availableCategories.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
        <span id="event-category-hint" className="filter-hint">
          Choose one or more. Pin colors identify categories.
        </span>
        {availableCategories.length > 0 ? (
          <ul className="category-legend" aria-label="Category color legend">
            {availableCategories.map((category) => (
              <li key={category}>
                <span
                  className="category-swatch"
                  style={{ backgroundColor: pinStyleForCategory(category).background }}
                  aria-hidden="true"
                />
                <span>{category}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </aside>
  );
}
