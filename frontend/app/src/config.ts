const DOMAIN = "irin-out-of-sleep-at-hackgt.tech";

export const RELAY_URL: string = import.meta.env.VITE_RELAY_URL ?? `https://api.${DOMAIN}`;
export const CLOUD_URL: string = import.meta.env.VITE_CLOUD_URL ?? `https://cloud.${DOMAIN}`;
// Development only: when set and the backend's /api/health reports hw: mock, the Device tab
// uses it directly, skips pairing, and shows a small DEV badge. Undefined unless set.
export const DEVICE_URL_OVERRIDE: string | undefined = import.meta.env.VITE_DEVICE_URL || undefined;
