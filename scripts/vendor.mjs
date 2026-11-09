// Copia las librerías JS de node_modules a static/vendor/ (funciona en Windows, macOS y Linux).
// Uso: npm install && npm run vendor
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const target = join(root, 'static', 'vendor');
mkdirSync(target, { recursive: true });

const files = [
  ['htmx.org/dist/htmx.min.js', 'htmx.min.js'],
  ['lightweight-charts/dist/lightweight-charts.standalone.production.js', 'lightweight-charts.standalone.production.js'],
];
for (const [source, name] of files) {
  copyFileSync(join(root, 'node_modules', source), join(target, name));
  console.log(`static/vendor/${name}`);
}
