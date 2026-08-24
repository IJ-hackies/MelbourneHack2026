"use server";

import { headers } from "next/headers";
import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { isRateLimited } from "@/lib/rate-limit";

async function clientIp(): Promise<string> {
  const h = await headers();
  return h.get("x-forwarded-for")?.split(",")[0].trim() ?? h.get("x-real-ip") ?? "unknown";
}

export async function logWalk({
  routeId,
  destination,
  minutes,
  distanceKm,
  guestStatId,
}: {
  routeId: string;
  destination: string;
  minutes: number;
  distanceKm: number;
  // Present when this walk was already counted anonymously (see
  // logGuestWalk) before the user signed in and claimed it -- that
  // guest_walk_stats row needs to go, or the community total double-counts
  // this same walk once as anonymous and once as this user's.
  guestStatId?: string | null;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { error: "Not signed in" };

  const emissionsKg = Number((distanceKm * 0.19).toFixed(2));

  const { error } = await supabase.from("walks").insert({
    user_id: user.id,
    route_id: routeId,
    destination,
    // walks.minutes is a smallint column, but route.minutes upstream
    // (api/route-planner.py's round(x, 1)) is a decimal like 30.2 — every
    // insert with a fractional value was silently rejected by Postgres,
    // which meant no signed-in walk was ever actually saved. Round here,
    // at the DB boundary, rather than changing what's displayed elsewhere.
    minutes: Math.round(minutes),
    distance_km: distanceKm,
    emissions_kg: emissionsKg,
  });

  if (error) return { error: error.message };

  if (guestStatId) {
    // Best-effort: if this fails, the walk is still saved correctly to the
    // user's own history, it just also double-counts once in the public
    // total until that row ages out on its own significance (never, but a
    // rare RPC failure here isn't worth blocking the real save over).
    await supabase.rpc("claim_guest_walk_stat", { p_id: guestStatId }).then(
      () => {},
      () => {}
    );
  }

  revalidatePath("/history");
  return { error: null };
}

// Counts a guest's completed walk toward the public community-impact
// total immediately, without waiting for (or requiring) a sign-in. No
// user_id, route, or destination is stored -- see the guest_walk_stats
// migration for exactly what's kept.
export async function logGuestWalk({ distanceKm }: { distanceKm: number }) {
  if (isRateLimited(`log-guest-walk:${await clientIp()}`, { windowMs: 60_000, maxPerWindow: 10 })) {
    return { id: null, error: "Too many requests." };
  }
  if (!(distanceKm > 0) || distanceKm > 100) return { id: null, error: "Invalid distance" };

  const supabase = await createClient();
  const emissionsKg = Number((distanceKm * 0.19).toFixed(2));

  const { data, error } = await supabase.rpc("create_guest_walk_stat", {
    p_distance_km: distanceKm,
    p_emissions_kg: emissionsKg,
  });

  if (error) return { id: null, error: error.message };
  return { id: data as string, error: null };
}

export async function deleteWalk(id: string) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { error: "Not signed in" };

  const { error } = await supabase
    .from("walks")
    .delete()
    .eq("id", id)
    .eq("user_id", user.id);

  if (error) return { error: error.message };

  revalidatePath("/history");
  return { error: null };
}
