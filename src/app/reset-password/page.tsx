import Link from "next/link";
import { redirect } from "next/navigation";
import { AccountForm } from "@/components/account-form";
import { changePassword } from "@/lib/actions/account";
import { createClient } from "@/lib/supabase/server";

export default async function ResetPassword() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-sm flex-col justify-center px-5 py-8 sm:px-8">
      <div className="mb-8">
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text">
          Set a new password
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Choose a new password for {user.email}.
        </p>
      </div>

      <AccountForm
        action={changePassword}
        submitLabel="Update password"
        field={{
          name: "password",
          label: "New password",
          type: "password",
          autoComplete: "new-password",
        }}
      />

      <p className="mt-6 text-center text-sm text-text-secondary">
        <Link href="/" className="font-medium text-primary">
          Continue to LeafRoute
        </Link>
      </p>
    </main>
  );
}
