import type { EventFilters } from "./events";

const DATE_PREFIX = /^\d{4}-\d{2}-\d{2}/;
const TIME_VALUE = /^([01]\d|2[0-3]):[0-5]\d$/;

function validDateTime(value: string | null): string | null {
  if (value === null || !DATE_PREFIX.test(value) || Number.isNaN(Date.parse(value))) {
    return null;
  }
  return value;
}

function validTime(value: string | null): string {
  return value !== null && TIME_VALUE.test(value) ? value : "";
}

export function readEventFilters(search = window.location.search): EventFilters {
  const parameters = new URLSearchParams(search);
  const categories = Array.from(
    new Set(
      (parameters.get("categories") ?? "")
        .split(",")
        .map((category) => category.trim())
        .filter((category) => category !== ""),
    ),
  );
  return {
    startsAfter: validDateTime(parameters.get("starts_after")),
    startsBefore: validDateTime(parameters.get("starts_before")),
    timeOfDayStart: validTime(parameters.get("time_of_day_start")),
    timeOfDayEnd: validTime(parameters.get("time_of_day_end")),
    categories,
  };
}

function setOrDelete(parameters: URLSearchParams, name: string, value: string | null): void {
  if (value === null || value === "") {
    parameters.delete(name);
  } else {
    parameters.set(name, value);
  }
}

export function replaceEventFilterUrl(filters: EventFilters): void {
  const url = new URL(window.location.href);
  setOrDelete(url.searchParams, "starts_after", filters.startsAfter);
  setOrDelete(url.searchParams, "starts_before", filters.startsBefore);
  setOrDelete(url.searchParams, "time_of_day_start", filters.timeOfDayStart);
  setOrDelete(url.searchParams, "time_of_day_end", filters.timeOfDayEnd);
  setOrDelete(
    url.searchParams,
    "categories",
    filters.categories.length > 0 ? filters.categories.join(",") : null,
  );
  window.history.replaceState(window.history.state, "", url);
}

export function startOfUtcDate(value: string): string | null {
  return value === "" ? null : `${value}T00:00:00.000Z`;
}

export function endOfUtcDate(value: string): string | null {
  return value === "" ? null : `${value}T23:59:59.999Z`;
}

export function dateInputValue(value: string | null): string {
  return value === null ? "" : value.slice(0, 10);
}
