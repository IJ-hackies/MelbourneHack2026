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

class LiveConditionProvider implements ConditionProvider {
  async getConditions(input: ConditionQueryInput): Promise<Condition[]> {
    const [weatherCondition, crowdCondition, shadeCondition] = await Promise.all([
      this.getWeatherCondition(input),
      this.getCrowdCondition(input),
      this.getShadeCondition(input),
    ]);
    return [weatherCondition, crowdCondition, shadeCondition];
  }

  private async getShadeCondition(input: ConditionQueryInput): Promise<Condition> {
    // Real tree-canopy-centroid density near the destination (see
    // api/shade.py, ml/routing/scripts/build_shade_grid.py) — a leafiness
    // proxy, not a precise solar-shade calculation, so it's labelled
    // "Canopy nearby" rather than "Shade %".
    try {
      const url = new URL("/api/shade", await getBaseUrl());
      url.searchParams.set("lat", String(input.lat));
      url.searchParams.set("lon", String(input.lon));

      const res = await fetch(url, { cache: "no-store" });
      const data: ShadeResponse = await res.json();

      if (!res.ok || data.status !== "ok" || data.canopy_density === null) {
        return { label: "Canopy nearby", value: "Unavailable", tone: "primary" };
      }

      return { label: "Canopy nearby", value: `${Math.round(data.canopy_density * 100)}%`, tone: "primary" };
    } catch {
      return { label: "Canopy nearby", value: "Unavailable", tone: "primary" };
    }
  }

  private async getWeatherCondition(input: ConditionQueryInput): Promise<Condition> {
    try {
      const url = new URL("/api/weather", await getBaseUrl());
      url.searchParams.set("lat", String(input.lat));
      url.searchParams.set("lon", String(input.lon));

      const res = await fetch(url, { cache: "no-store" });
      const data: WeatherResponse = await res.json();

      if (!res.ok || !data.conditions) {
        return { label: "Feels hot", value: "Unavailable", tone: "heat" };
      }

      return {
        label: "Feels hot",
        value: `${Math.round(data.conditions.temperatureC)}°C`,
        tone: "heat",
      };
    } catch {
      return { label: "Feels hot", value: "Unavailable", tone: "heat" };
    }
  }

  private async getCrowdCondition(input: ConditionQueryInput): Promise<Condition> {
    // Raw pedestrian-flow-per-hour at the nearest live sensor, not a route
    // or area density figure — labelled generically ("Nearby") rather than
    // implying it describes the destination itself.
    const signal = await callCrowdInference({ lat: input.lat, lon: input.lon }, new Date());
    if (signal.qualityStatus === "unavailable") {
      return { label: "Crowds", value: "Unavailable", tone: "crowd" };
    }
    return {
      label: "Crowds nearby",
      value: `${Math.round(signal.pedestrianFlowPerHour)}/hr`,
      tone: "crowd",
    };
  }
}

export const conditionProvider: ConditionProvider = new LiveConditionProvider();
