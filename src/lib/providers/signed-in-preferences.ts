import { createClient } from "@/lib/supabase/server";
import type { UserPreferences } from "./types";

// Only a signed-in user's saved preferences actually bias routing — a guest
// gets the same sensible defaults the routing function already applies.
// Shared by every server-side caller that plans routes (the plan page's
// /api/plan-routes and the route detail page) so a signed-in user's
// preferences apply consistently everywhere routes get generated, not just
// on the screen where they were first picked.
export async function loadSignedInPreferences(): Promise<Partial<UserPreferences> | undefined> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return undefined;

  const { data: profile } = await supabase
    .from("profiles")
    .select("heat_sensitivity, comfort_balance, pace, prefer_quieter_streets, prefer_lower_traffic")
    .eq("id", user.id)
    .maybeSingle();
  if (!profile) return undefined;

  return {
    heatSensitivity: profile.heat_sensitivity ?? undefined,
    comfortBalance: profile.comfort_balance ?? undefined,
    pace: profile.pace ?? undefined,
    preferQuieterStreets: profile.prefer_quieter_streets ?? undefined,
    preferLowerTraffic: profile.prefer_lower_traffic ?? undefined,
  };
}
