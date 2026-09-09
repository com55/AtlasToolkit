// Runs every browser test file in sequence via node's own child_process,
// not shell chaining -- sidesteps two problems shell separators have:
//
// 1. `&&` between commands aborts the whole chain on the first non-zero
//    exit. verify-ui-flows.mjs has a project-documented flaky baseline of
//    3-4 failures that occur on nearly every run (see CLAUDE.md), so under
//    `&&` it silently killed every suite listed after it -- confirmed only
//    2 of 8 suites ever actually ran via `npm run test:browser` before this
//    file existed.
// 2. `;` (the fix for #1) runs every suite regardless of an earlier one's
//    exit code, which is correct -- but `;` is a POSIX/PowerShell statement
//    separator, not a cmd.exe one (npm's default script shell on Windows).
//    Under cmd.exe, everything after the first `.mjs` path in a `;`-joined
//    command line becomes inert argv text passed to the FIRST script, so
//    only verify-ops.mjs would run, silently, with no error.
//
// Doing the sequencing here in Node instead avoids both: every suite always
// runs, and the aggregate exit code (unlike a bare `;`-joined command,
// which only reflects the LAST suite) correctly reflects whether ANY
// suite failed.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

const SUITES = [
  'verify-ops.mjs',
  'verify-ui-flows.mjs',
  'verify-region-identity.mjs',
  'verify-mesh-repack.mjs',
  'verify-mesh-mask-processor.mjs',
  'verify-nest-repack.mjs',
  'verify-repack-asymmetry.mjs',
  'verify-region-editing.mjs',
];

let anyFailed = false;
const results = [];

for (const suite of SUITES) {
  const result = spawnSync('node', [path.join(HERE, suite)], { stdio: 'inherit' });
  const code = result.status ?? 1;
  if (code !== 0) anyFailed = true;
  results.push({ suite, code });
}

console.log('\n─── run-all summary ───');
for (const { suite, code } of results) {
  console.log(`${code === 0 ? 'PASS' : 'FAIL'}  ${suite} (exit ${code})`);
}
console.log(anyFailed
  ? '\nAt least one suite reported failures above -- verify-ui-flows.mjs\'s own documented 3-4-item flaky baseline is expected and not itself a regression; any OTHER failure is real.'
  : '\nAll suites passed.');

process.exit(anyFailed ? 1 : 0);
