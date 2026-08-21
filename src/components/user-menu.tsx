import { logout } from "@/lib/actions/auth";

export function UserMenu({ email }: { email: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-sm text-text-secondary sm:inline">{email}</span>
      <form action={logout}>
        <button
          type="submit"
          className="rounded-full border border-border bg-surface-alt px-3.5 py-2 text-[0.82rem] font-medium text-text-secondary transition-colors hover:text-text"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
