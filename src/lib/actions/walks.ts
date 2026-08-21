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
    minutes,
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
