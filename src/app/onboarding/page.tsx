import { redirect } from "next/navigation";
import { PreferencesForm } from "@/components/preferences-form";
import { FormStatusButton } from "@/components/form-status-button";
import { completeOnboarding, skipOnboarding } from "@/lib/actions/profile";
import { defaultProfile } from "@/lib/profile";
import { createClient } from "@/lib/supabase/server";

export default async function Onboarding() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("display_name, heat_sensitivity, comfort_balance, pace, prefer_quieter_streets, prefer_lower_traffic, calendar_suggestions")
    .eq("id", user.id)
    .single();

  const name = profile?.display_name;

  return (
    <main className="mx-auto flex max-w-md flex-col gap-8 px-5 py-8 sm:px-8 lg:py-12">
      <div>
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
          {name ? `Welcome, ${name}` : "Welcome to LeafRoute"}
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          A few quick preferences to set up. Sensible defaults are already
          in place, so it&apos;s fine to skip ahead too.
        </p>
      </div>

      <PreferencesForm
        profile={profile ?? defaultProfile}
        action={completeOnboarding}
        submitLabel="Start planning routes"
        layout="onboarding"
      />

      <form action={skipOnboarding} className="text-center">
        <FormStatusButton pendingLabel="Skipping…" className="text-sm text-text-secondary underline">
          Skip for now, use sensible defaults
        </FormStatusButton>
      </form>
    </main>
  );
}
