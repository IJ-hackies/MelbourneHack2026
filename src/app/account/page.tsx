import { redirect } from "next/navigation";
import { DeleteAccountForm } from "@/components/delete-account-form";
import { ResetLinkButton } from "@/components/reset-link-button";
import { sendPasswordResetLink } from "@/lib/actions/account";
import { createClient } from "@/lib/supabase/server";

export default async function Account() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-10 px-5 py-8 sm:px-8 lg:max-w-2xl lg:py-12">
      <div>
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text lg:text-[1.9rem]">
          Account
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Signed in as <span className="text-text">{user.email}</span>
        </p>
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-text">
          Change password
        </h2>
        <p className="mt-1 mb-3 text-[0.82rem] text-text-tertiary">
          We&apos;ll email you a secure link to set a new password.
        </p>
        <ResetLinkButton action={sendPasswordResetLink} label="Send reset link" />
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-heat">
          Delete account
        </h2>
        <p className="mt-1 mb-3 text-[0.82rem] text-text-tertiary">
          This can&apos;t be undone.
        </p>
        <DeleteAccountForm />
      </div>
    </main>
  );
}
