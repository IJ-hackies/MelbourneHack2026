import type { Condition, ConditionQueryInput } from "./types";

export interface ConditionProvider {
  getConditions(input: ConditionQueryInput): Promise<Condition[]>;
}

// Fixed sample conditions regardless of destination, until live weather,
// crowd-sensor, and shade-geometry data sources exist. Swap `conditionProvider`
// below for a real implementation — every caller goes through this interface.
const STUB_CONDITIONS: Condition[] = [
  { label: "Feels hot", value: "31°C", tone: "heat" },
  { label: "Crowds", value: "Med", tone: "crowd" },
  { label: "Shade", value: "62%", tone: "primary" },
];

class StubConditionProvider implements ConditionProvider {
  async getConditions(input: ConditionQueryInput): Promise<Condition[]> {
    void input; // stub ignores the query; a real provider would use it
    return STUB_CONDITIONS;
  }
}

export const conditionProvider: ConditionProvider = new StubConditionProvider();
