export function base64ToFile(base64, filename, mime) {
  const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  return new File([bytes], filename, { type: mime });
}

export function pathToFileUrl(path) {
  let p = String(path).replace(/\\/g, '/');
  if (!p.startsWith('/')) p = '/' + p; // Windows: "C:/foo" -> "/C:/foo"
  const segments = p.split('/').map((seg, i) => (
    i === 1 && /^[a-zA-Z]:$/.test(seg) ? seg : encodeURIComponent(seg)
  ));
  return `file://${segments.join('/')}`;
}

export async function loadFileAsFile(path, mime = 'image/png') {
  const url = pathToFileUrl(path);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Failed to read local file: ${path}`);
  const blob = await response.blob();
  const name = String(path).replace(/\\/g, '/').split('/').pop();
  return new File([blob], name, { type: mime });
}
