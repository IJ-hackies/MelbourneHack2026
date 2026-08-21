export const DEFAULT_DESTINATION = "Fitzroy Gardens";

export function formatDeparture(date: Date) {
  const time = date.toLocaleTimeString("en-AU", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  return `Leaving now · ${time.toLowerCase()}`;
}
