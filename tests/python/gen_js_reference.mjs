/**
 * Cross-language parity helper -- runs the real www/js/atlas-document.js
 * (via plain Node, no browser needed: this module has no DOM dependency)
 * against a JSON payload of {name, atlasText} cases piped in on stdin, and
 * prints {name: serializedOutput} as JSON on stdout. Invoked by
 * test_document_js_parity.py via subprocess, not run standalone.
 */
import { AtlasDocument } from '../../www/js/atlas-document.js';

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const cases = JSON.parse(input);
const out = {};
for (const [name, atlasText] of Object.entries(cases)) {
  out[name] = AtlasDocument.parse(atlasText).serialize();
}
process.stdout.write(JSON.stringify(out));
