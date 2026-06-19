import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** dist-electron directory at runtime (ESM-safe replacement for __dirname). */
export const ELECTRON_ROOT = path.dirname(fileURLToPath(import.meta.url));
