"use server";

import { Resend } from "resend";

export type ContactState = { error: string | null; success?: boolean } | undefined;

const CONTACT_INBOX = process.env.CONTACT_INBOX_EMAIL ?? "hello@leafroute.org";

export async function sendContactMessage(
  _prevState: ContactState,
  formData: FormData
): Promise<ContactState> {
  const name = String(formData.get("name") ?? "").trim();
  const email = String(formData.get("email") ?? "").trim();
  const message = String(formData.get("message") ?? "").trim();

  if (!name || !email || !message) {
    return { error: "Fill in every field." };
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return { error: "Enter a valid email address." };
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    return { error: "Email isn't configured yet, try again shortly." };
  }

  const resend = new Resend(apiKey);
  const { error } = await resend.emails.send({
    from: "LeafRoute <onboarding@resend.dev>",
    to: CONTACT_INBOX,
    replyTo: email,
    subject: `LeafRoute contact form from ${name}`,
    text: `From: ${name} <${email}>\n\n${message}`,
  });

  if (error) {
    return { error: "Couldn't send that, try again in a moment." };
  }

  return { error: null, success: true };
}
