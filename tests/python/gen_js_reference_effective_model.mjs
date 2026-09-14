/**
 * Cross-language parity helper for domain/effective_model.py -- runs the
 * real www/js/effective-region-model.js (plain Node, no DOM) against a JSON
 * payload of {name: {region_meta, mod_batches}} piped in on stdin (mod_batches
 * items use the plan's snake_case dict shape; this script translates them
 * into the real ModBatch/AddBatch/RemoveBatch/RenameBatch instances before
 * calling deriveEffectiveModel, since the real function dispatches on
 * `batch.type` plus camelCase fields) and prints {name: serializedOutput}
 * as JSON, with modifiedKeys converted to a sorted array for stable JSON
 * comparison (Sets have no native JSON representation). Invoked by
 * test_effective_model_js_parity.py via subprocess.
 */
import { deriveEffectiveModel } from '../../www/js/effective-region-model.js';
import { ModBatch, AddBatch, RemoveBatch, RenameBatch } from '../../www/js/atlas-session.js';

function toRealBatch(b) {
  switch (b.type) {
    case 'mod': return new ModBatch(b.names, b.source ?? null);
    case 'add': return new AddBatch(b.internal_key, b.atlas_name, b.source_canvas ?? null);
    case 'remove': return new RemoveBatch(b.target_key);
    case 'rename': return new RenameBatch(b.target_key, b.new_atlas_name);
    default: throw new Error(`unknown batch type: ${b.type}`);
  }
}

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const cases = JSON.parse(input);
const out = {};
for (const [name, spec] of Object.entries(cases)) {
  const batches = (spec.mod_batches || []).map(toRealBatch);
  const m = deriveEffectiveModel(spec.region_meta, batches);
  out[name] = {
    region_names: m.regionNames,
    regions: m.regions,
    labels: m.labels,
    modified_keys: [...m.modifiedKeys].sort(),
  };
}
process.stdout.write(JSON.stringify(out));
