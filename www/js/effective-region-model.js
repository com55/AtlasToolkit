/**
 * Folds pristine region metadata + an ordered structural/pixel batch list
 * into the "effective" model every consumer (packer, sidebar list,
 * modified-highlight) reads instead of the pristine atlas alone. Spec §2.3.
 *
 * Deliberately shape-agnostic on `regionMeta`'s values: this function only
 * ever reads/copies `.atlasName` and spreads the rest of each RegionMeta
 * object through untouched — it never reads position/size fields, so both
 * an unscaled AtlasProcessor.regions map (cheap, used for listing) and a
 * scaled AtlasModifier.regions map (needed for packing) are valid inputs.
 * See spec §2.3's "modifierRegions is shape-agnostic" note (round 5 finding 4).
 *
 * @param {{[key: string]: object}} regionMeta  pristine region metadata,
 *   keyed by internal key. Never mutated.
 * @param {Array<import('./atlas-session.js').ModBatch
 *              | import('./atlas-session.js').AddBatch
 *              | import('./atlas-session.js').RemoveBatch
 *              | import('./atlas-session.js').RenameBatch>} modBatches
 * @returns {{ regionNames: string[], regions: {[key:string]: object},
 *             labels: {[key:string]: string}, modifiedKeys: Set<string> }}
 */
export function deriveEffectiveModel(regionMeta, modBatches) {
  const regionNames = Object.keys(regionMeta); // stable order: pristine parse order
  const regions = {};
  for (const key of regionNames) regions[key] = regionMeta[key];
  const labels = {};
  const modifiedKeys = new Set();
  const removed = new Set();

  for (const batch of modBatches || []) {
    switch (batch.type) {
      case 'mod':
        for (const name of batch.names) modifiedKeys.add(name);
        break;
      case 'add': {
        if (!regionNames.includes(batch.internalKey)) regionNames.push(batch.internalKey);
        regions[batch.internalKey] = {
          atlasName: batch.atlasName,
          offsets: null,
          index: -1,
          split: null,
          pad: null,
          extraPairs: [],
        };
        labels[batch.internalKey] = batch.atlasName;
        modifiedKeys.add(batch.internalKey);
        break;
      }
      case 'remove':
        removed.add(batch.targetKey);
        break;
      case 'rename': {
        const existing = regions[batch.targetKey];
        if (existing) {
          regions[batch.targetKey] = { ...existing, atlasName: batch.newAtlasName };
        }
        labels[batch.targetKey] = batch.newAtlasName;
        modifiedKeys.add(batch.targetKey);
        break;
      }
      default:
        break;
    }
  }

  const finalNames = regionNames.filter((k) => !removed.has(k));
  const finalRegions = {};
  for (const k of finalNames) finalRegions[k] = regions[k];
  for (const k of removed) { delete labels[k]; modifiedKeys.delete(k); }

  return { regionNames: finalNames, regions: finalRegions, labels, modifiedKeys };
}
