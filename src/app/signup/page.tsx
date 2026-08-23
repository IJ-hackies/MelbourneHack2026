import Link from "next/link";
import { AuthForm } from "@/components/auth-form";
import { GoogleButton } from "@/components/google-button";
import { signup } from "@/lib/actions/auth";
import { safeLocalPath } from "@/lib/safe-redirect";

export default async function Signup({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-sm flex-col justify-center px-5 py-8 sm:px-8">
      <div className="mb-8">
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text">
          Create your account
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Sensible defaults from the start, personalise after.
        </p>
      </div>

      <GoogleButton />

      <div className="my-6 flex items-center gap-3 text-xs text-text-tertiary">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>

      <AuthForm
        action={signup}
        submitLabel="Create account"
        fields={[
          { name: "displayName", label: "Name", type: "text", autoComplete: "name" },
          { name: "email", label: "Email", type: "email", autoComplete: "email" },
          {
            name: "password",
            label: "Password",
            type: "password",
            autoComplete: "new-password",
          },
        ]}
      />

      <p className="mt-6 text-center text-sm text-text-secondary">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-primary">
          Sign in
        </Link>
      </p>

      <p className="mt-4 text-center text-sm">
        <Link href={safeLocalPath(next)} className="font-medium text-text-secondary hover:text-text">
          Continue as guest
        </Link>
      </p>
    </main>
  );
}
