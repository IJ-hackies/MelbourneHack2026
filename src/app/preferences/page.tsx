import { redirect } from "next/navigation";
import { PreferencesForm } from "@/components/preferences-form";
import { savePreferences } from "@/lib/actions/profile";
import { defaultProfile } from "@/lib/profile";
import { createClient } from "@/lib/supabase/server";

export default async function Preferences() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select(
      "heat_sensitivity, comfort_balance, pace, prefer_quieter_streets, prefer_lower_traffic, calendar_suggestions"
    )
    .eq("id", user.id)
    .single();

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-8 px-5 py-8 sm:px-8 lg:max-w-4xl lg:py-12">
      <div>
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
          Your preferences
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Change anytime. These decide which route we recommend for you out of the options a
          search finds — they never change how many routes you get or what they are.
        </p>
      </div>

      <PreferencesForm
        profile={profile ?? defaultProfile}
        action={savePreferences}
        submitLabel="Save preferences"
      />
    </main>
  );
}
