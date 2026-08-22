import { test, expect } from "@playwright/test";
import { TEST_EMAIL, TEST_PASSWORD } from "./helpers";

test("health check responds", async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.status).toBe("ok");
});

test("guest reaches the Plan screen without signing in", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "Where to?" })).toBeVisible();
});

test("guest is sent to login only when opening an account-only page", async ({ page }) => {
  await page.goto("/history");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
});

test("guest visiting an unknown route sees a 404, not a login redirect", async ({ page }) => {
  await page.goto("/this-route-does-not-exist");
  await expect(page).toHaveURL("/this-route-does-not-exist");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});

test("signed-in visitor reaches the Plan screen, then sees 404 for an unknown route", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(TEST_EMAIL);
  await page.getByLabel("Password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });
  await page.reload();

  const heading = page.getByRole("heading", { name: /Where to\?|Welcome/ });
  await expect(heading).toBeVisible();

  if ((await heading.textContent())?.startsWith("Welcome")) {
    await page.getByRole("button", { name: "Skip for now, use sensible defaults" }).click();
    await page.waitForURL((url) => url.pathname === "/", { timeout: 15_000 });
  }

  await page.goto("/this-route-does-not-exist");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});
