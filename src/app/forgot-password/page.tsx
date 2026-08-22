import Link from "next/link";
import { AuthForm } from "@/components/auth-form";
import { requestPasswordReset } from "@/lib/actions/auth";

export default function ForgotPassword() {
  return (
    <main className="mx-auto flex min-h-[calc(100vh-105px)] max-w-sm flex-col justify-center px-5 py-8 sm:px-8">
      <div className="mb-8">
        <h1 className="font-display text-[1.6rem] font-semibold tracking-tight text-text">
          Reset your password
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Enter your email and we&apos;ll send you a link to set a new one.
        </p>
      </div>

      <AuthForm
        action={requestPasswordReset}
        submitLabel="Send reset link"
        fields={[{ name: "email", label: "Email", type: "email", autoComplete: "email" }]}
      />

      <p className="mt-6 text-center text-sm text-text-secondary">
        <Link href="/login" className="font-medium text-primary">
          Back to sign in
        </Link>
      </p>
    </main>
  );
}
