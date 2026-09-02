/**
 * Region-name validation per docs/superpowers/specs/2026-09-01-advanced-
 * region-editing-design.md §2.4. Canonicalizes (trims) once up front so the
 * same string is checked, compared, and eventually written — this is what
 * closes the whitespace-evasion class round 3 found (e.g. " arm " passing
 * validation against "arm" then colliding with it after a trim on reload).
 *
 * The '#' ban is a TOOL POLICY, not a parser fact: the parser happily
 * accepts a literal '#' in a name and only ever *generates* one itself
 * during parse-time dedup of duplicate literal names. Banning it from every
 * name this tool writes means the tool can never produce a string that
 * collides with a parser-synthesized key on reparse (round 2's collision
 * class; round 3 finding 10 corrected the false "parser invariant" framing).
 *
 * @param {string} candidate
 * @param {Iterable<string>} effectiveDisplayNames  every OTHER region's
 *   current effective display name (pristine ∪ pending Add/Rename results),
 *   never including the candidate's own prior name.
 * @returns {{ok: true, value: string} | {ok: false, reason: string}}
 */
export function validateRegionName(candidate, effectiveDisplayNames) {
  const value = String(candidate ?? '').trim();

  if (value === '') {
    return { ok: false, reason: 'Name cannot be blank.' };
  }
  if (/[\n\r]/.test(value)) {
    return { ok: false, reason: 'Name cannot contain a line break.' };
  }
  if (value.includes(':')) {
    return { ok: false, reason: "Name cannot contain ':'." };
  }
  if (/\.png$/i.test(value)) {
    return { ok: false, reason: "Name cannot end in '.png'." };
  }
  if (value.includes('#')) {
    return { ok: false, reason: "Name cannot contain '#'." };
  }
  for (const other of effectiveDisplayNames) {
    if (String(other).trim() === value) {
      return { ok: false, reason: `'${value}' is already in use.` };
    }
  }
  return { ok: true, value };
}
