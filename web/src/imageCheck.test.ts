import { describe, expect, it } from "vitest";
import { findProblems, laplacianVariance, meanBrightness, toGray } from "./imageCheck";

function image(width: number, height: number, pixel: (x: number, y: number) => number): Float32Array {
  const gray = new Float32Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) gray[y * width + x] = pixel(x, y);
  return gray;
}

describe("laplacianVariance", () => {
  it("is zero for a flat image", () => {
    expect(laplacianVariance(image(20, 20, () => 128), 20, 20)).toBe(0);
  });

  it("scores sharp edges far above a smooth gradient", () => {
    const stripes = image(40, 40, (x) => (x % 2 ? 255 : 0)); // like crisp printed text
    const gradient = image(40, 40, (x) => x * 6); // like a blurred edge
    expect(laplacianVariance(stripes, 40, 40)).toBeGreaterThan(10_000);
    expect(laplacianVariance(gradient, 40, 40)).toBeLessThan(1);
  });
});

describe("toGray and meanBrightness", () => {
  it("converts RGBA to luma", () => {
    const rgba = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]);
    const gray = toGray(rgba);
    expect(gray[0]).toBeCloseTo(255);
    expect(gray[1]).toBe(0);
    expect(meanBrightness(gray)).toBeCloseTo(127.5);
  });
});

describe("findProblems", () => {
  it("passes a sharp, bright, large photo", () => {
    expect(findProblems(1700, 160, 1536)).toEqual([]);
  });

  it("flags each problem", () => {
    expect(findProblems(27, 160, 1536)).toEqual(["blurry"]);
    expect(findProblems(1700, 45, 1536)).toEqual(["dark"]);
    expect(findProblems(1700, 160, 400)).toEqual(["small"]);
  });
});
