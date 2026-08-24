"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

// Clamps a form field to a valid integer in [min, max], falling back to
// `fallback` for anything unparseable (NaN, missing) rather than writing a
// value that could silently corrupt the row or feed a nonsensical bias into
// route scoring.
function clampedInt(formData: FormData, key: string, fallback: number, min: number, max: number): number {
  const parsed = Number(formData.get(key));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function readPreferences(formData: FormData) {
  return {
    heat_sensitivity: clampedInt(formData, "heat_sensitivity", 50, 0, 100),
    comfort_balance: clampedInt(formData, "comfort_balance", 50, 0, 100),
    pace: clampedInt(formData, "pace", 2, 0, 4),
    prefer_quieter_streets: formData.get("prefer_quieter_streets") === "true",
    prefer_lower_traffic: formData.get("prefer_lower_traffic") === "true",
  };
}

export async function savePreferences(_prevState: unknown, formData: FormData) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { error } = await supabase
    .from("profiles")
    .update(readPreferences(formData))
    .eq("id", user.id);

  revalidatePath("/preferences");
  return { error: error?.message ?? null, saved: !error };
}

export async function skipOnboarding() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  await supabase.from("profiles").update({ onboarded: true }).eq("id", user.id);
  revalidatePath("/", "layout");
  redirect("/");
}

export async function completeOnboarding(_prevState: unknown, formData: FormData) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { error } = await supabase
    .from("profiles")
    .update({ ...readPreferences(formData), onboarded: true })
    .eq("id", user.id);

  if (error) {
    return { error: error.message };
  }

  revalidatePath("/", "layout");
  redirect("/");
}
