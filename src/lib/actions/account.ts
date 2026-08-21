"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

export type AccountState = { error: string | null; success?: string } | undefined;

export async function changeEmail(
  _prevState: AccountState,
  formData: FormData
): Promise<AccountState> {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) return { error: "Enter a new email address." };

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ email });

  if (error) return { error: error.message };

  return {
    error: null,
    success: "Check both your old and new inbox to confirm the change.",
  };
}

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
