import { createClient } from "@supabase/supabase-js";
import { LOCAL_SUPABASE_SECRET_KEY, LOCAL_SUPABASE_URL, TEST_EMAIL, TEST_PASSWORD } from "./helpers";

export default async function globalSetup() {
  const admin = createClient(LOCAL_SUPABASE_URL, LOCAL_SUPABASE_SECRET_KEY);

  const { error } = await admin.auth.admin.createUser({
    email: TEST_EMAIL,
    password: TEST_PASSWORD,
    email_confirm: true,
    user_metadata: { display_name: "Playwright" },
  });

  if (error && error.code !== "email_exists") {
    throw error;
  }
}
