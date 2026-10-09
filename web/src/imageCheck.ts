/**
 * Pre-upload checks (docs/PLAN.md, defense 1): catch blurry, dark, or tiny
 * photos on the phone, where retaking is cheap, instead of paying Textract to
 * read them. Also re-encodes every photo as a JPEG of at most 2000 px, which
 * converts iPhone HEIC and keeps uploads small.
 */

export const MAX_SIDE = 2000;
export const ANALYSIS_SIDE = 500;
export const MIN_SHORT_SIDE = 600;
// Calibrated on Phase 1 receipt photos at ANALYSIS_SIDE: sharp ≈ 1,700,
// slightly blurred but readable ≈ 220-290, clearly blurred ≈ 27; normal
// brightness ≈ 150-167, darkened to 30% ≈ 45-50. Warnings only: the user
// can still upload.
export const MIN_SHARPNESS = 60; // variance of the Laplacian; lower = blurrier
export const MIN_BRIGHTNESS = 50; // mean grey level, 0-255

export type Problem = "blurry" | "dark" | "small";

export interface PreparedPhoto {
  blob: Blob;
  previewUrl: string;
  problems: Problem[];
  sharpness: number;
  brightness: number;
}

/** Luma (0-255) for RGBA pixel data. */
export function toGray(rgba: Uint8ClampedArray): Float32Array {
  const gray = new Float32Array(rgba.length / 4);
  for (let i = 0; i < gray.length; i++) {
    gray[i] = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
  }
  return gray;
}

export function meanBrightness(gray: Float32Array): number {
  let sum = 0;
  for (const value of gray) sum += value;
  return gray.length ? sum / gray.length : 0;
}

/** Variance of the 4-neighbour Laplacian: a standard, cheap sharpness score. */
export function laplacianVariance(gray: Float32Array, width: number, height: number): number {
  let sum = 0;
  let sumSq = 0;
  let n = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const lap = gray[i - width] + gray[i + width] + gray[i - 1] + gray[i + 1] - 4 * gray[i];
      sum += lap;
      sumSq += lap * lap;
      n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  return sumSq / n - mean * mean;
}

export function findProblems(sharpness: number, brightness: number, shortSide: number): Problem[] {
  const problems: Problem[] = [];
  if (sharpness < MIN_SHARPNESS) problems.push("blurry");
  if (brightness < MIN_BRIGHTNESS) problems.push("dark");
  if (shortSide < MIN_SHORT_SIDE) problems.push("small");
  return problems;
}

function scaled(width: number, height: number, maxSide: number): [number, number] {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return [Math.round(width * scale), Math.round(height * scale)];
}

async function loadImage(file: Blob): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode(); // browsers apply EXIF orientation when drawing
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function preparePhoto(file: Blob): Promise<PreparedPhoto> {
  const img = await loadImage(file);
  const { naturalWidth: width, naturalHeight: height } = img;

  // Quality checks on a small copy: fast, and the thresholds are tuned at this size.
  const [aw, ah] = scaled(width, height, ANALYSIS_SIDE);
  const small = document.createElement("canvas");
  small.width = aw;
  small.height = ah;
  const ctx = small.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("Canvas isn't available");
  ctx.drawImage(img, 0, 0, aw, ah);
  const gray = toGray(ctx.getImageData(0, 0, aw, ah).data);
  const sharpness = laplacianVariance(gray, aw, ah);
  const brightness = meanBrightness(gray);

  const [ow, oh] = scaled(width, height, MAX_SIDE);
  const out = document.createElement("canvas");
  out.width = ow;
  out.height = oh;
  out.getContext("2d")!.drawImage(img, 0, 0, ow, oh);
  const blob = await new Promise<Blob>((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't encode the photo"))), "image/jpeg", 0.85),
  );

  return {
    blob,
    previewUrl: URL.createObjectURL(blob),
    problems: findProblems(sharpness, brightness, Math.min(width, height)),
    sharpness,
    brightness,
  };
}
