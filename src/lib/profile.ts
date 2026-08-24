export const paceLabels = ["Slow", "Relaxed", "Steady", "Brisk", "Fast"];

export type Profile = {
  id: string;
  display_name: string | null;
  onboarded: boolean;
  heat_sensitivity: number;
  comfort_balance: number;
  pace: number;
  prefer_quieter_streets: boolean;
  prefer_lower_traffic: boolean;
};

export const defaultProfile: Omit<Profile, "id"> = {
  display_name: null,
  onboarded: false,
  heat_sensitivity: 50,
  comfort_balance: 50,
  pace: 2,
  prefer_quieter_streets: true,
  prefer_lower_traffic: true,
};
