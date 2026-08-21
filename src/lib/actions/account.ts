"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

export type AccountState = { error: string | null; success?: string } | undefined;

// Signature matches useActionState's (state, formData) shape; neither is
// needed since the reset always targets the current session's own email.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function sendPasswordResetLink(_prevState: AccountState, _formData: FormData): Promise<AccountState> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user?.email) redirect("/login");

  const headerList = await headers();
  const origin = headerList.get("origin") ?? process.env.NEXT_PUBLIC_SITE_URL ?? "";

  const { error } = await supabase.auth.resetPasswordForEmail(user.email, {
    redirectTo: `${origin}/auth/callback?next=/reset-password`,
  });

  if (error) return { error: error.message };

  return { error: null, success: "Check your inbox for a link to set a new password." };
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
