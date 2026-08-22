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

// Shade has no implementation yet — it's a geometry/solar calculation, not
// an ML signal, and no shade service exists. Keep this a clearly-labelled
// placeholder rather than pretending it's live.
const PLACEHOLDER_CONDITIONS: Condition[] = [{ label: "Shade", value: "62%", tone: "primary" }];

class LiveConditionProvider implements ConditionProvider {
  async getConditions(input: ConditionQueryInput): Promise<Condition[]> {
    const [weatherCondition, crowdCondition] = await Promise.all([
      this.getWeatherCondition(input),
      this.getCrowdCondition(input),
    ]);
    return [weatherCondition, crowdCondition, ...PLACEHOLDER_CONDITIONS];
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
