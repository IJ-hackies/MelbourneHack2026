import { redirect } from "next/navigation";
import { AccountForm } from "@/components/account-form";
import { DeleteAccountForm } from "@/components/delete-account-form";
import { changeEmail, changePassword } from "@/lib/actions/account";
import { createClient } from "@/lib/supabase/server";

export default async function Account() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const hasPassword = user.app_metadata?.provider === "email";

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
          Change email
        </h2>
        <p className="mt-1 mb-3 text-[0.82rem] text-text-tertiary">
          You&apos;ll need to confirm the change from both inboxes.
        </p>
        <AccountForm
          action={changeEmail}
          submitLabel="Update email"
          field={{ name: "email", label: "New email", type: "email", autoComplete: "email" }}
        />
      </div>

      <div>
        <h2 className="font-display text-base font-semibold tracking-tight text-text">
          {hasPassword ? "Change password" : "Set a password"}
        </h2>
        <p className="mt-1 mb-3 text-[0.82rem] text-text-tertiary">
          {hasPassword
            ? "Update the password you use to sign in."
            : "You signed up with Google, add a password to also sign in with email."}
        </p>
        <AccountForm
          action={changePassword}
          submitLabel={hasPassword ? "Update password" : "Set password"}
          field={{
            name: "password",
            label: "New password",
            type: "password",
            autoComplete: "new-password",
          }}
        />
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
