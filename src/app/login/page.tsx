import Link from "next/link";
import { AuthForm } from "@/components/auth-form";
import { login } from "@/lib/actions/auth";

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;

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

      <p className="mt-6 text-center text-sm text-text-secondary">
        New to HeatRoute?{" "}
        <Link href="/signup" className="font-medium text-primary">
          Create an account
        </Link>
      </p>
    </main>
  );
}
