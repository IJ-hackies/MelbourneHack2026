"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

export async function logWalk({
  routeId,
  destination,
  minutes,
  distanceKm,
}: {
  routeId: string;
  destination: string;
  minutes: number;
  distanceKm: number;
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

  revalidatePath("/history");
  return { error: null };
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
