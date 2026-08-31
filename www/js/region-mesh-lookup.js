/** Narrows a parsed skeleton's full attachment map down to just the
 *  Mesh-type entries this feature can mask with. Region/BoundingBox/
 *  LinkedMesh/Path/Point/Clipping are dropped here — callers treat a
 *  missing lookup entry as "no mask data for this region," same as any
 *  other unavailable case.
 *  @param {{attachments: Map<string, {type: string, path: string, uvs?: number[], triangles?: number[]}>}} parsedSkeleton
 *  @returns {Map<string, {uvs: number[], triangles: number[]}>}
 */
export function buildMeshLookup(parsedSkeleton) {
  const lookup = new Map();
  for (const [name, info] of parsedSkeleton.attachments) {
    if (info.type === 'Mesh') lookup.set(name, { uvs: info.uvs, triangles: info.triangles });
  }
  return lookup;
}
