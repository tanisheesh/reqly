// dist/cjs holds CommonJS output inside a "type": "module" package; this
// marker makes Node load those .js files as CommonJS.
import { writeFileSync } from "node:fs";

writeFileSync(new URL("../dist/cjs/package.json", import.meta.url), JSON.stringify({ type: "commonjs" }) + "\n");
