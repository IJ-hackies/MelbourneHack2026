import { loginWithGoogle } from "@/lib/actions/auth";
import { GoogleSubmitButton } from "@/components/google-submit-button";

export function GoogleButton() {
  return (
    <form action={loginWithGoogle}>
      <GoogleSubmitButton />
    </form>
  );
}
