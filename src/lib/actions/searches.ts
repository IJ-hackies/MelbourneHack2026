"use server";

import { createClient } from "@/lib/supabase/server";

export type RecentSearch = {
  label: string;
  address: string | null;
  lat: number | null;
  lon: number | null;
};

export async function listRecentSearches(): Promise<RecentSearch[]> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return [];

  const { data } = await supabase
    .from("recent_searches")
    .select("label, address, lat, lon")
    .eq("user_id", user.id)
    .order("searched_at", { ascending: false })
    .limit(5);

  return data ?? [];
}

export async function logSearch(place: {
  label: string;
  address?: string;
  lat?: number;
  lon?: number;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return;

  await supabase.from("recent_searches").upsert(
    {
      user_id: user.id,
      label: place.label,
      address: place.address ?? null,
      lat: place.lat ?? null,
      lon: place.lon ?? null,
      searched_at: new Date().toISOString(),
    },
    { onConflict: "user_id, label" }
  );
}
