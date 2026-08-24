"use client";

import { useActionState, useEffect, useState } from "react";
import { Slider } from "@/components/slider";
import { Toggle } from "@/components/toggle";
import { useToast } from "@/components/toast-provider";
import { Spinner } from "@/components/spinner";
import { paceLabels, type Profile } from "@/lib/profile";

type ActionState = { error: string | null; saved?: boolean } | undefined;

export function PreferencesForm({
  profile,
  action,
  submitLabel,
  layout = "settings",
}: {
  profile: Omit<Profile, "id" | "display_name" | "onboarded">;
  action: (state: ActionState, formData: FormData) => Promise<ActionState>;
  submitLabel: string;
  layout?: "settings" | "onboarding";
}) {
  const [state, formAction, pending] = useActionState(action, undefined);
  const showToast = useToast();

  useEffect(() => {
    if (state?.saved) showToast("Preferences saved");
  }, [state, showToast]);

  const [heatSensitivity, setHeatSensitivity] = useState(profile.heat_sensitivity);
  const [comfortBalance, setComfortBalance] = useState(profile.comfort_balance);
  const [pace, setPace] = useState(profile.pace);
  const [quieterStreets, setQuieterStreets] = useState(profile.prefer_quieter_streets);
  const [lowerTraffic, setLowerTraffic] = useState(profile.prefer_lower_traffic);
  const [calendarSuggestions, setCalendarSuggestions] = useState(
    profile.calendar_suggestions
  );

  const slidersClass =
    layout === "onboarding"
      ? "flex flex-col gap-6"
      : "grid grid-cols-1 gap-6 lg:grid-cols-2 lg:gap-x-16 lg:gap-y-6";
  const togglesClass =
    layout === "onboarding"
      ? "flex flex-col gap-2"
      : "grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-x-16";

  return (
    <form action={formAction} className="flex flex-col gap-8">
      <input type="hidden" name="heat_sensitivity" value={heatSensitivity} />
      <input type="hidden" name="comfort_balance" value={comfortBalance} />
      <input type="hidden" name="pace" value={pace} />
      <input type="hidden" name="prefer_quieter_streets" value={String(quieterStreets)} />
      <input type="hidden" name="prefer_lower_traffic" value={String(lowerTraffic)} />
      <input
        type="hidden"
        name="calendar_suggestions"
        value={String(calendarSuggestions)}
      />

      <div className={slidersClass}>
        <Slider
          label="Heat & sun sensitivity"
          value={heatSensitivity}
          onChange={setHeatSensitivity}
          valueLabel={
            heatSensitivity < 33 ? "LOW" : heatSensitivity < 67 ? "MEDIUM" : "HIGH"
          }
          hint="Higher sensitivity means we'll recommend the shaded route by default, when one's available."
        />
        <Slider
          label="Speed vs. comfort"
          value={comfortBalance}
          onChange={setComfortBalance}
          valueLabel={comfortBalance < 50 ? "COMFORT" : "SPEED"}
          hint="Leaning toward comfort recommends the shaded or quieter option first, when one exists. This doesn't change which routes are offered."
        />
        <Slider
          label="Walking pace"
          value={pace}
          min={0}
          max={4}
          onChange={setPace}
          valueLabel={paceLabels[pace].toUpperCase()}
          hint="Saved for future ETA tuning. It doesn&apos;t affect route choices yet."
          comingSoon
        />
      </div>

      <div className={togglesClass}>
        <div>
          {layout === "settings" && (
            <h2 className="font-display text-base font-semibold tracking-tight text-text">
              Recommendation priority
            </h2>
          )}
          <Toggle
            name="Prefer quieter streets"
            description="When a quieter option exists, we'll recommend it first instead of fastest. This doesn't change which routes are offered."
            checked={quieterStreets}
            onChange={setQuieterStreets}
          />
          <Toggle
            name="Prefer lower traffic"
            description="Saved for when vehicle-traffic routing ships. It doesn&apos;t affect route choices yet."
            checked={lowerTraffic}
            onChange={setLowerTraffic}
            comingSoon
          />
        </div>

        <div>
          {layout === "settings" && (
            <h2 className="font-display text-base font-semibold tracking-tight text-text">
              Integrations
            </h2>
          )}
          <Toggle
            name="Calendar suggestions"
            description="Not built yet, so this can't be turned on. Once it ships, it'll only read your next event's time and location, and always ask you to confirm before routing."
            checked={calendarSuggestions}
            onChange={setCalendarSuggestions}
            comingSoon
            disabled
          />
        </div>
      </div>

      {state?.error && (
        <p className="rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">
          {state.error}
        </p>
      )}

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 rounded-2xl bg-primary py-3.5 text-center font-semibold text-surface shadow-[0_10px_22px_-12px_hsl(160_30%_15%/0.45)] disabled:opacity-60 lg:max-w-sm"
      >
        {pending && <Spinner className="h-4 w-4 text-surface" />}
        {pending ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
