/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_COLLECTOR_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
