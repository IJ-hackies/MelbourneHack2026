"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

export type SavedPlace = {
  id: string;
  kind: "home" | "work" | "favorite";
  label: string;
  address: string | null;
  lat: number | null;
  lon: number | null;
};

export async function listSavedPlaces(): Promise<SavedPlace[]> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return [];

  const { data } = await supabase
    .from("saved_places")
    .select("id, kind, label, address, lat, lon")
    .eq("user_id", user.id)
    .order("created_at", { ascending: true });

  return data ?? [];
}

export async function savePlace({
  kind,
  label,
  address,
  lat,
  lon,
}: {
  kind: "home" | "work" | "favorite";
  label: string;
  address?: string;
  lat?: number;
  lon?: number;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { error: "Not signed in" };

  // Home/Work are single slots per user — replace rather than append.
  // Delete-then-insert atomically via an RPC (see the partial unique
  // indexes + replace_saved_slot() in the saved_places migrations), so a
  // failure mid-swap can't wipe a slot without replacing it.
  if (kind !== "favorite") {
    const { error } = await supabase.rpc("replace_saved_slot", {
      p_kind: kind,
      p_label: label,
      p_address: address ?? null,
      p_lat: lat ?? null,
      p_lon: lon ?? null,
    });

    if (error) return { error: error.message };

    revalidatePath("/");
    return { error: null };
  }

  const { error } = await supabase.from("saved_places").insert({
    user_id: user.id,
    kind,
    label,
    address: address ?? null,
    lat: lat ?? null,
    lon: lon ?? null,
  });

  if (error) return { error: error.message };

  revalidatePath("/");
  return { error: null };
}

export async function deleteSavedPlace(id: string) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { error: "Not signed in" };

  const { error } = await supabase
    .from("saved_places")
    .delete()
    .eq("id", id)
    .eq("user_id", user.id);

  if (error) return { error: error.message };

  revalidatePath("/");
  return { error: null };
}
