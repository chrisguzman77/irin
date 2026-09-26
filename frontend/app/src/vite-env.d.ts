/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_RELAY_URL?: string;
  readonly VITE_CLOUD_URL?: string;
  readonly VITE_FAMILY_URL?: string;
  readonly VITE_DEVICE_URL?: string;
  readonly VITE_FB_APP_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
