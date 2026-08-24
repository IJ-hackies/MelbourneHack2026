import { getBaseUrl } from "@/lib/base-url";
import { callCrowdInference } from "@/lib/ml/ml-client";
import type { Condition, ConditionQueryInput } from "./types";

export interface ConditionProvider {
  getConditions(input: ConditionQueryInput): Promise<Condition[]>;
}

type WeatherResponse = {
  conditions: {
    temperatureC: number;
    relativeHumidityPct: number;
    windSpeedMs: number;
    uvIndex: number;
  } | null;
  error?: string;
};

type ShadeResponse = { canopy_density: number | null; status: "ok" | "unavailable" };

// The real fetched temperature deserves a real descriptor, not a hardcoded
// "Feels hot" regardless of value (12°C previously said exactly that).
function feelsLabel(tempC: number): string {
  if (tempC < 10) return "Feels cold";
  if (tempC < 17) return "Feels cool";
  if (tempC < 23) return "Feels mild";
  if (tempC < 28) return "Feels warm";
  return "Feels hot";
}

// The standard WHO/EPA UV Index bands used on Australian sun-safety advice
// (sunsmart.com.au uses the same 3/6/8/11 cut-offs) — "protection needed"
// starts at 3, not just at the "extreme" end, so this reflects real
// sun-safety guidance rather than only flagging the worst case.
function uvLabel(uv: number): string {
  if (uv < 3) return "Low UV";
  if (uv < 6) return "Moderate UV";
  if (uv < 8) return "High UV";
  if (uv < 11) return "Very high UV";
  return "Extreme UV";
}

// US EPA AQI bands, same scale Open-Meteo's air-quality API reports
// (us_aqi) — used as-is rather than inventing a different cutoff scheme.
function airQualityLabel(aqi: number): string {
  if (aqi <= 50) return "Good air quality";
  if (aqi <= 100) return "Moderate air quality";
  if (aqi <= 150) return "Unhealthy for sensitive groups";
  if (aqi <= 200) return "Unhealthy air quality";
  if (aqi <= 300) return "Very unhealthy air quality";
  return "Hazardous air quality";
}

type AirQualityResponse = {
  conditions: { usAqi: number; pm25: number } | null;
};

class LiveConditionProvider implements ConditionProvider {
  async getConditions(input: ConditionQueryInput): Promise<Condition[]> {
    const [weatherConditions, crowdCondition, shadeCondition, airQualityCondition] = await Promise.all([
      this.getWeatherConditions(input),
      this.getCrowdCondition(input),
      this.getShadeCondition(input),
      this.getAirQualityCondition(input),
    ]);
    return [...weatherConditions, airQualityCondition, crowdCondition, shadeCondition];
  }

  private async getAirQualityCondition(input: ConditionQueryInput): Promise<Condition> {
    // Real, current PM2.5-derived AQI (Open-Meteo's air-quality API, same
    // provider already used for weather, no API key required) — the
    // "helping people cope with the climate impacts already happening" half
    // of the app's climate-action framing, since bushfire smoke is exactly
    // that kind of impact and previously had no surface here at all.
    try {
      const url = new URL("/api/air-quality", await getBaseUrl());
      url.searchParams.set("lat", String(input.lat));
      url.searchParams.set("lon", String(input.lon));

      const res = await fetch(url, { cache: "no-store" });
      const data: AirQualityResponse = await res.json();

      if (!res.ok || !data.conditions) {
        return { label: "Air quality", value: "Unavailable", tone: "primary" };
      }

      const { usAqi, pm25 } = data.conditions;
      return {
        label: airQualityLabel(usAqi),
        value: `AQI ${Math.round(usAqi)}`,
        detail: `PM2.5: ${pm25.toFixed(1)} µg/m³`,
        tone: "primary",
      };
    } catch {
      return { label: "Air quality", value: "Unavailable", tone: "primary" };
    }
  }

  private async getShadeCondition(input: ConditionQueryInput): Promise<Condition> {
    // Real tree-canopy-centroid density near the destination (see
    // api/shade.py, ml/routing/scripts/build_shade_grid.py). It's a real
    // measurement of tree cover, just not a precise solar-angle shadow
    // calculation — "Shade" is still the honest everyday word for it
    // (nobody reading a walking app expects sub-metre solar geometry), so
    // it's labelled plainly with the canopy-derived caveat moved to the
    // detail line instead of the label itself.
    try {
      const url = new URL("/api/shade", await getBaseUrl());
      url.searchParams.set("lat", String(input.lat));
      url.searchParams.set("lon", String(input.lon));

      const res = await fetch(url, { cache: "no-store" });
      const data: ShadeResponse = await res.json();

      if (!res.ok || data.status !== "ok" || data.canopy_density === null) {
        return { label: "Shade", value: "Unavailable", tone: "primary" };
      }

      return {
        label: "Shade",
        value: `${Math.round(data.canopy_density * 100)}%`,
        detail: "Tree canopy nearby",
        tone: "primary",
      };
    } catch {
      return { label: "Shade", value: "Unavailable", tone: "primary" };
    }
  }

  // Fetched once and split into two conditions (temperature, UV) — both
  // come from the same /api/weather call, so this avoids firing a second
  // request just to read a different field off the same response.
  private async getWeatherConditions(input: ConditionQueryInput): Promise<Condition[]> {
    try {
      const url = new URL("/api/weather", await getBaseUrl());
      url.searchParams.set("lat", String(input.lat));
      url.searchParams.set("lon", String(input.lon));

      const res = await fetch(url, { cache: "no-store" });
      const data: WeatherResponse = await res.json();

      if (!res.ok || !data.conditions) {
        return [
          { label: "Temperature", value: "Unavailable", tone: "heat" },
          { label: "UV index", value: "Unavailable", tone: "heat" },
        ];
      }

      const { temperatureC, uvIndex } = data.conditions;
      return [
        { label: feelsLabel(temperatureC), value: `${Math.round(temperatureC)}°C`, tone: "heat" },
        {
          label: uvLabel(uvIndex),
          value: uvIndex.toFixed(1),
          detail: uvIndex >= 8 ? "Sun protection recommended" : undefined,
          tone: "heat",
        },
      ];
    } catch {
      return [
        { label: "Temperature", value: "Unavailable", tone: "heat" },
        { label: "UV index", value: "Unavailable", tone: "heat" },
      ];
    }
  }

  private async getCrowdCondition(input: ConditionQueryInput): Promise<Condition> {
    // Raw pedestrian-flow-per-hour at the nearest live sensor, not a route
    // or area density figure. A bare count ("84/hr") means little without
    // context, so the headline value is a real comparison against that same
    // sensor's own rolling 168h average — never a fabricated scale — and
    // the raw figure moves to the detail line for anyone who wants it.
    // callCrowdInference already catches its own fetch/parse failures and
    // resolves to an "unavailable" signal rather than throwing, but this
    // guards the contract locally too (matching the other three sibling
    // methods) so a future change there can't turn a single degraded
    // condition into a Promise.all rejection that blanks every tile.
    try {
      const signal = await callCrowdInference({ lat: input.lat, lon: input.lon }, new Date());
      if (signal.qualityStatus === "unavailable") {
        return { label: "Crowds nearby", value: "Unavailable", tone: "crowd" };
      }

      const rate = Math.round(signal.pedestrianFlowPerHour);

      if (signal.typicalFlowPerHour === null || signal.typicalFlowPerHour <= 0) {
        return { label: "Crowds nearby", value: `${rate}/hr`, tone: "crowd" };
      }

      // typicalFlowPerHour is a flat rolling 7-day average across every hour
      // (see feature_lookup.py's flow_rolling_past_168h_mean — every hour of
      // the past week, not filtered to this same hour/weekday), so the detail
      // line says exactly that rather than implying a day-specific baseline
      // ("usual Wednesday afternoon") the underlying number doesn't support.
      const detail = `${rate} people/hr nearby, vs. ${Math.round(signal.typicalFlowPerHour)}/hr average this week`;
      const ratio = signal.pedestrianFlowPerHour / signal.typicalFlowPerHour;
      const value = ratio >= 1.3 ? "Busier than usual" : ratio <= 0.7 ? "Quieter than usual" : "Typical crowds";
      return { label: "Crowds nearby", value, detail, tone: "crowd" };
    } catch {
      return { label: "Crowds nearby", value: "Unavailable", tone: "crowd" };
    }
  }
}

export const conditionProvider: ConditionProvider = new LiveConditionProvider();
