// Upstream colors, fonts, radii and shadows are reused without alteration.
import upstream from './upstream.tailwind.config.js';
import { fileURLToPath } from 'node:url';

export default {
  ...upstream,
  content: [fileURLToPath(new URL('../helper/static/*.{html,js}', import.meta.url)),
    fileURLToPath(new URL('../site/*.{js,mjs,html}', import.meta.url))]
};
