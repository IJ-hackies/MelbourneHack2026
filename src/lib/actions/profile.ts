"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";

function readPreferences(formData: FormData) {
  return {
    heat_sensitivity: Number(formData.get("heat_sensitivity") ?? 50),
    comfort_balance: Number(formData.get("comfort_balance") ?? 50),
    pace: Number(formData.get("pace") ?? 2),
    prefer_quieter_streets: formData.get("prefer_quieter_streets") === "true",
    prefer_lower_traffic: formData.get("prefer_lower_traffic") === "true",
    calendar_suggestions: formData.get("calendar_suggestions") === "true",
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

  redirect("/");
}
