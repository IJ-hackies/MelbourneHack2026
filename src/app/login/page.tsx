import Link from "next/link";
import { AuthForm } from "@/components/auth-form";
import { GoogleButton } from "@/components/google-button";
import { login } from "@/lib/actions/auth";

const ERROR_MESSAGES: Record<string, string> = {
  oauth: "Something went wrong signing in with Google. Please try again.",
};

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  const errorMessage = error ? (ERROR_MESSAGES[error] ?? "Something went wrong. Please try again.") : null;

  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-sm flex-col justify-center px-5 py-8 sm:px-8">
      <div className="mb-8">
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text">
          Welcome back
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Sign in to plan your next walk.
        </p>
      </div>

      {errorMessage && (
        <p className="mb-6 rounded-xl bg-heat-soft px-3.5 py-2.5 text-sm text-heat">
          {errorMessage}
        </p>
      )}

      <GoogleButton />

      <div className="my-6 flex items-center gap-3 text-xs text-text-tertiary">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>

      <AuthForm
        action={login}
        submitLabel="Sign in"
        hidden={{ next: next ?? "/" }}
        fields={[
          { name: "email", label: "Email", type: "email", autoComplete: "email" },
          {
            name: "password",
            label: "Password",
            type: "password",
            autoComplete: "current-password",
          },
        ]}
      />

      <p className="mt-3 text-right text-sm">
        <Link href="/forgot-password" className="font-medium text-primary">
          Forgot password?
        </Link>
      </p>

      <p className="mt-6 text-center text-sm text-text-secondary">
        New to LeafRoute?{" "}
        <Link href="/signup" className="font-medium text-primary">
          Create an account
        </Link>
      </p>
    </main>
  );
}
