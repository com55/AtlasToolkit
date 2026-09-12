/**
 * Cross-language parity helper -- runs the real www/js/atlas-document.js
 * (via plain Node, no browser needed: this module has no DOM dependency)
 * against a JSON payload of {name: {fn, ...args}} cases piped in on
 * stdin, dispatching each to parse+serialize, withUpdates, or
 * fromRebuildArgs, and prints {name: serializedOutput} as JSON on
 * stdout. Invoked by test_document_js_parity.py via subprocess, not run
 * standalone. Covers all three of atlas-document.js's exported entry
 * points (not just parse/serialize) so this harness can actually reach
 * the functions Task 5's scrutinize review found real bugs in.
 */
import { AtlasDocument, pngNamesForSave } from '../../www/js/atlas-document.js';

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const cases = JSON.parse(input);
const out = {};
for (const [name, spec] of Object.entries(cases)) {
  if (spec.fn === 'parse_serialize') {
    out[name] = AtlasDocument.parse(spec.atlas_text).serialize();
  } else if (spec.fn === 'with_updates') {
    const doc = AtlasDocument.parse(spec.atlas_text);
    const updated = doc.withUpdates(spec.updated_regions, spec.page_size ?? null);
    out[name] = updated.serialize();
  } else if (spec.fn === 'from_rebuild_args') {
    const doc = AtlasDocument.fromRebuildArgs(
      spec.page_info, spec.new_size, spec.region_names, spec.region_data
    );
    out[name] = doc.serialize();
  } else if (spec.fn === 'png_names_for_save') {
    out[name] = pngNamesForSave(spec.atlas_text, spec.page_count, spec.atlas_filename);
  } else {
    throw new Error(`unknown fn: ${spec.fn}`);
  }
}
process.stdout.write(JSON.stringify(out));
