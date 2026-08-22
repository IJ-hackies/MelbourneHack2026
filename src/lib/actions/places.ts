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

  const payload = {
    user_id: user.id,
    kind,
    label,
    address: address ?? null,
    lat: lat ?? null,
    lon: lon ?? null,
  };

  // Home/Work are single slots per user — replace rather than append.
  // (A partial unique index backs this at the DB level too; Postgres can't
  // target ON CONFLICT at a partial index's implicit predicate from here,
  // so it's enforced with an explicit delete-then-insert instead of upsert.)
  if (kind !== "favorite") {
    await supabase
      .from("saved_places")
      .delete()
      .eq("user_id", user.id)
      .eq("kind", kind);
  }

  const { error } = await supabase.from("saved_places").insert(payload);

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
