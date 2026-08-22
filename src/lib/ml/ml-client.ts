import { getBaseUrl } from "@/lib/base-url";
import type { CrowdSignal, TrafficSignal } from "@/lib/providers/types";

type CrowdInferenceResponse = {
  prediction: { pedestrian_flow_per_hour: number } | null;
  quality: { status: "ok" | "degraded" | "unavailable"; warnings: string[] };
};

type TrafficInferenceResponse = {
  prediction: { vehicle_count_per_hour: number } | null;
  quality: { status: "ok" | "degraded" | "unavailable"; warnings: string[] };
};

const UNAVAILABLE_CROWD: CrowdSignal = {
  pedestrianFlowPerHour: 0,
  qualityStatus: "unavailable",
  warnings: ["crowd_inference_unreachable"],
};

const UNAVAILABLE_TRAFFIC: TrafficSignal = {
  vehicleCountPerHour: 0,
  qualityStatus: "unavailable",
  warnings: ["traffic_inference_unreachable"],
};

export async function callCrowdInference(
  coordinates: { lat: number; lon: number },
  targetHour: Date
): Promise<CrowdSignal> {
  try {
    const res = await fetch(new URL("/api/crowd-inference", await getBaseUrl()), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Uses the software adapter's lat/lon convenience extension, which
      // resolves the nearest live sensor before predicting — the app only
      // knows a destination's coordinates, never a raw sensor_id.
      body: JSON.stringify({
        lat: coordinates.lat,
        lon: coordinates.lon,
        target_hour: targetHour.toISOString(),
      }),
      cache: "no-store",
    });
    const data: CrowdInferenceResponse = await res.json();
    if (!data.prediction) {
      return { pedestrianFlowPerHour: 0, qualityStatus: data.quality.status, warnings: data.quality.warnings };
    }
    return {
      pedestrianFlowPerHour: data.prediction.pedestrian_flow_per_hour,
      qualityStatus: data.quality.status,
      warnings: data.quality.warnings,
    };
  } catch {
    return UNAVAILABLE_CROWD;
  }
}

export async function callTrafficInference(
  sourceGroup: string,
  observationUnitId: string,
  targetHour: Date
): Promise<TrafficSignal> {
  try {
    const res = await fetch(new URL("/api/traffic-inference", await getBaseUrl()), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_group: sourceGroup,
        observation_unit_id: observationUnitId,
        target_hour: targetHour.toISOString(),
      }),
      cache: "no-store",
    });
    const data: TrafficInferenceResponse = await res.json();
    if (!data.prediction) {
      return { vehicleCountPerHour: 0, qualityStatus: data.quality.status, warnings: data.quality.warnings };
    }
    return {
      vehicleCountPerHour: data.prediction.vehicle_count_per_hour,
      qualityStatus: data.quality.status,
      warnings: data.quality.warnings,
    };
  } catch {
    return UNAVAILABLE_TRAFFIC;
  }
}
