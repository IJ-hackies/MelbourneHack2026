// A guest who finishes a walk before signing in previously just lost it —
// ActiveWalk told them to sign in to save it, but there was nothing to
// claim once they did. This is the shared shape/key for stashing that one
// completed walk in localStorage (finished on the client, in active-walk.tsx)
// and claiming it once a session exists (claim-pending-walk.tsx).
export const PENDING_WALK_STORAGE_KEY = "leafroute_pending_walk";

export type PendingWalk = {
  routeId: string;
  destination: string;
  minutes: number;
  distanceKm: number;
  // Matches the id of the row this walk was already counted under in
  // guest_walk_stats (see lib/actions/walks.ts's logGuestWalk) -- claiming
  // this pending walk into the signed-in user's real history needs to
  // delete that row first, or the same walk counts twice toward the public
  // community total.
  guestStatId: string | null;
};
