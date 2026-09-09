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
let anySkipped = false;
const results = [];

for (const suite of SUITES) {
  // Captured (not 'inherit') so this runner can tell a genuine pass from a
  // self-skip (playwright-core absent, ground_truth_ops.json missing, etc
  // -- several suites print 'SKIP: ...' and exit 0 in that case, same as a
  // real pass). Without this, a fresh clone with no playwright gets an
  // authoritative-looking "All suites passed" summary having tested
  // nothing in a browser -- the individual suites are honest about this,
  // the aggregate wasn't. Still echoed live so nothing about the visible
  // per-suite output changes.
  const result = spawnSync('node', [path.join(HERE, suite)], { encoding: 'utf8' });
  const output = (result.stdout || '') + (result.stderr || '');
  process.stdout.write(output);
  const code = result.status ?? 1;
  // A suite that ran real checks may still print a PARTIAL 'SKIP N case(s)'
  // line (e.g. verify-ops.mjs skipping its .workspaces/-dependent
  // real-world cases while its other checks genuinely ran) -- that's not
  // a whole-suite self-skip. Only treat it as one when the suite produced
  // NO 'PASS' line at all, i.e. it tested nothing whatsoever this run.
  const skipped = code === 0 && /^SKIP\b/m.test(output) && !/^PASS\b/m.test(output);
  if (code !== 0) anyFailed = true;
  if (skipped) anySkipped = true;
  results.push({ suite, code, skipped });
}

console.log('\n─── run-all summary ───');
for (const { suite, code, skipped } of results) {
  const label = code !== 0 ? 'FAIL' : skipped ? 'SKIP' : 'PASS';
  console.log(`${label}  ${suite} (exit ${code})`);
}
console.log(anyFailed
  ? '\nAt least one suite reported failures above -- verify-ui-flows.mjs\'s own documented 3-4-item flaky baseline is expected and not itself a regression; any OTHER failure is real.'
  : anySkipped
    ? '\nNo suite failed, but at least one SKIPPED (see above) -- that suite tested nothing this run (playwright-core or a fixture file was missing), not a pass. Set $PLAYWRIGHT_CORE and re-run for real coverage.'
    : '\nAll suites passed.');

process.exit(anyFailed ? 1 : 0);
