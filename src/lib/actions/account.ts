"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

export type AccountState = { error: string | null; success?: string } | undefined;

export async function changePassword(
  _prevState: AccountState,
  formData: FormData
): Promise<AccountState> {
  const password = String(formData.get("password") ?? "");
  if (password.length < 8) {
    return { error: "Password must be at least 8 characters." };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ password });

  if (error) return { error: error.message };

  return { error: null, success: "Password updated." };
}

// Supabase's admin API doesn't expose a per-session "list your active
// devices" endpoint, but `signOut({ scope: "others" })` genuinely revokes
// every refresh token except the one making this call — a real, if coarse,
// substitute for multi-device session management until a listing API exists.
export async function signOutOtherSessions(): Promise<{ error: string | null; success?: string }> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { error } = await supabase.auth.signOut({ scope: "others" });
  if (error) return { error: error.message };
  return { error: null, success: "Signed out everywhere else." };
}

// Data portability ahead of the (irreversible) account deletion below — a
// user's profile, preferences, saved places, recent searches, and walk
// history, exactly as stored, so they have a real copy before choosing to
// delete it. Returned as a plain object for a client component to turn into
// a downloadable file, since a Server Action can't hand back a file stream.
export async function exportAccountData(): Promise<{ error: string | null; data?: string }> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [profile, savedPlaces, recentSearches, walks] = await Promise.all([
    supabase.from("profiles").select("*").eq("id", user.id).maybeSingle(),
    supabase.from("saved_places").select("*").eq("user_id", user.id),
    supabase.from("recent_searches").select("*").eq("user_id", user.id),
    supabase.from("walks").select("*").eq("user_id", user.id),
  ]);

  const firstError =
    profile.error ?? savedPlaces.error ?? recentSearches.error ?? walks.error ?? null;
  if (firstError) return { error: firstError.message };

  const payload = {
    exported_at: new Date().toISOString(),
    account: { id: user.id, email: user.email, created_at: user.created_at },
    profile: profile.data,
    saved_places: savedPlaces.data,
    recent_searches: recentSearches.data,
    walks: walks.data,
  };

  return { error: null, data: JSON.stringify(payload, null, 2) };
}

export async function deleteAccount(_prevState: AccountState, formData: FormData) {
  const confirmation = String(formData.get("confirmation") ?? "");
  if (confirmation !== "DELETE") {
    return { error: 'Type "DELETE" to confirm.' };
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const admin = createAdminClient();
  const { error } = await admin.auth.admin.deleteUser(user.id);
  if (error) return { error: error.message };

  await supabase.auth.signOut();
  redirect("/login");
}
