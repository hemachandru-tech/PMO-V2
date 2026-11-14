/**
 * The main colours of a logo (ENH-006, US-150). The browser decodes the logo (PNG, JPEG or SVG alike), so the colours
 * are read here and sent to the server as seeds for its palettes; the server does all the contrast and colour-blind
 * checks.
 */

export interface LogoColour {
  hex: string;
  /** Share of the logo's counted pixels, 0–1. */
  share: number;
}

const toHex = (r: number, g: number, b: number) => `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;

/**
 * Dominant colours in RGBA pixel data, most common first. Transparent, near-white and near-black pixels are skipped
 * (they are background and outlines, not brand colour); similar shades are merged; colours under 1 % are dropped.
 */
export function extractColours(rgba: Uint8ClampedArray | number[], max = 8): LogoColour[] {
  const buckets = new Map<number, { n: number; r: number; g: number; b: number }>();
  let counted = 0;
  for (let i = 0; i + 3 < rgba.length; i += 4) {
    const r = rgba[i]!, g = rgba[i + 1]!, b = rgba[i + 2]!, a = rgba[i + 3]!;
    if (a < 128) continue;
    const hi = Math.max(r, g, b), lo = Math.min(r, g, b);
    if (lo > 235 || hi < 28) continue;
    counted++;
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    const cell = buckets.get(key) ?? { n: 0, r: 0, g: 0, b: 0 };
    cell.n++; cell.r += r; cell.g += g; cell.b += b;
    buckets.set(key, cell);
  }
  const clusters: { n: number; r: number; g: number; b: number }[] = [];
  for (const cell of [...buckets.values()].sort((x, y) => y.n - x.n)) {
    const [r, g, b] = [cell.r / cell.n, cell.g / cell.n, cell.b / cell.n];
    const near = clusters.find((c) => Math.hypot(c.r / c.n - r, c.g / c.n - g, c.b / c.n - b) < 40);
    if (near) {
      near.n += cell.n; near.r += cell.r; near.g += cell.g; near.b += cell.b;
    } else {
      clusters.push({ ...cell });
    }
  }
  return clusters
    .filter((c) => c.n / counted >= 0.01)
    .sort((x, y) => y.n - x.n)
    .slice(0, max)
    .map((c) => ({ hex: toHex(c.r / c.n, c.g / c.n, c.b / c.n), share: c.n / counted }));
}

/** A logo's pixels, drawn at most `size` pixels on its longer side (enough for colours, and quick). */
export async function logoPixels(url: string, size = 96): Promise<Uint8ClampedArray> {
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = url;
  await img.decode();
  const w = img.naturalWidth || size, h = img.naturalHeight || size;
  const scale = Math.min(1, size / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round(h * scale));
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("This browser can't read the logo's colours.");
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
}
