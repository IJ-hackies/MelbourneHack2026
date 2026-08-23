"use client";

import { useState, useTransition } from "react";
import { exportAccountData } from "@/lib/actions/account";
import { useToast } from "@/components/toast-provider";
import { Spinner } from "@/components/spinner";

export function ExportDataButton() {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const showToast = useToast();

  function handleExport() {
    setError(null);
    startTransition(async () => {
      const result = await exportAccountData();
      if (result.error || !result.data) {
        setError(result.error ?? "Couldn't export your data, try again.");
        return;
      }
      const blob = new Blob([result.data], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `leafroute-data-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showToast("Your data has been downloaded");
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={handleExport}
        disabled={pending}
        aria-busy={pending}
        className="flex items-center justify-center gap-2 self-start rounded-xl border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-text disabled:opacity-60"
      >
        {pending && <Spinner className="h-3.5 w-3.5 text-current" />}
        {pending ? "Preparing export…" : "Export my data"}
      </button>
      {error && <p className="text-[0.82rem] text-heat">{error}</p>}
    </div>
  );
}
