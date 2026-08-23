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
};
