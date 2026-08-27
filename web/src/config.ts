function requiredEnvironmentValue(value: string | undefined, name: string): string {
  if (value === undefined || value.trim() === "") {
    throw new Error(`${name} is required`);
  }
  return value;
}

export interface AppConfig {
  apiBaseUrl: string;
  googleMapsApiKey: string;
  googleMapsMapId: string;
}

export function loadConfig(): AppConfig {
  return {
    apiBaseUrl: requiredEnvironmentValue(import.meta.env.VITE_API_BASE_URL, "VITE_API_BASE_URL"),
    googleMapsApiKey: requiredEnvironmentValue(
      import.meta.env.VITE_GOOGLE_MAPS_API_KEY,
      "VITE_GOOGLE_MAPS_API_KEY",
    ),
    googleMapsMapId: requiredEnvironmentValue(
      import.meta.env.VITE_GOOGLE_MAPS_MAP_ID,
      "VITE_GOOGLE_MAPS_MAP_ID",
    ),
  };
}
