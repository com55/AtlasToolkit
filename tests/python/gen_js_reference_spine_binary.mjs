/**
 * Cross-language parity helper for spine_binary -- runs the real vendored
 * www/js/vendor/spine-skeleton-binary/index.js (plain Node, no DOM) against
 * a JSON payload of {name: {fixture}} piped in on stdin, where `fixture` is
 * "synthetic-skeleton-38" or "synthetic-skeleton-42" (read from
 * tests/fixtures/ by this script itself, not embedded in the payload --
 * binary files don't round-trip cleanly through JSON). Prints
 * {name: {version, attachments}} as JSON, with the attachments Map
 * converted to a plain object for JSON comparison. Invoked by
 * test_spine_binary_parity.py via subprocess.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSkeleton } from '../../www/js/vendor/spine-skeleton-binary/index.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, '..', 'fixtures');

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const cases = JSON.parse(input);
const out = {};
for (const [name, spec] of Object.entries(cases)) {
  const bytes = new Uint8Array(readFileSync(path.join(FIXTURES, `${spec.fixture}.skel`)));
  const { version, attachments } = parseSkeleton(bytes);
  out[name] = { version, attachments: Object.fromEntries(attachments) };
}
process.stdout.write(JSON.stringify(out));
