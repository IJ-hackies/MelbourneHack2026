import { createClient } from "@supabase/supabase-js";

// Service-role client — bypasses RLS entirely. Only ever call this from
// server code that has already established which user is making the
// request (e.g. via the cookie-scoped client), and only to act on that
// same user's own account.
export function createAdminClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SECRET_KEY!,
    { auth: { autoRefreshToken: false, persistSession: false } }
  );
}
