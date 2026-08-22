"use client";

import { useEffect, useRef, useState } from "react";

export type LiveLocationState =
  | { status: "unsupported" }
  | { status: "prompt" }
  | { status: "denied" }
  | { status: "unavailable" }
  | { status: "tracking"; lat: number; lon: number; accuracy: number };

// Wraps navigator.geolocation.watchPosition. Never fabricates a position —
// every non-"tracking" state is a distinct, honest reason there isn't one
// (unsupported browser, permission denied, or a real position error/timeout),
// so callers can render a clear message instead of silently doing nothing.
const isGeolocationSupported = () =>
  typeof navigator !== "undefined" && Boolean(navigator.geolocation);

export function useLiveLocation(enabled: boolean): LiveLocationState {
  const [state, setState] = useState<LiveLocationState>(() =>
    isGeolocationSupported() ? { status: "prompt" } : { status: "unsupported" }
  );
  const watchIdRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || !isGeolocationSupported()) return;

    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        setState({
          status: "tracking",
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracy: position.coords.accuracy,
        });
      },
      (error) => {
        setState(error.code === error.PERMISSION_DENIED ? { status: "denied" } : { status: "unavailable" });
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 10000 }
    );
    watchIdRef.current = watchId;

    return () => {
      if (watchIdRef.current !== null) {
        navigator.geolocation.clearWatch(watchIdRef.current);
        watchIdRef.current = null;
      }
    };
  }, [enabled]);

  return state;
}
