const DOMAIN = "irin-out-of-sleep-at-hackgt.tech";

export const RELAY_URL: string = import.meta.env.VITE_RELAY_URL ?? `https://api.${DOMAIN}`;
export const CLOUD_URL: string = import.meta.env.VITE_CLOUD_URL ?? `https://cloud.${DOMAIN}`;
// The family view page a Level 2 recipient's link opens (frontend/family/).
export const FAMILY_URL: string = import.meta.env.VITE_FAMILY_URL ?? `https://family.${DOMAIN}`;
// Development only: when set and the backend's /api/health reports hw: mock, the Device tab
// uses it directly, skips pairing, and shows a small DEV badge. Undefined unless set.
export const DEVICE_URL_OVERRIDE: string | undefined = import.meta.env.VITE_DEVICE_URL || undefined;
