import { fileURLToPath } from "node:url";

import sharp from "sharp";

const standardSource = fileURLToPath(new URL("../public/icons/icon-source.svg", import.meta.url));
const maskableSource = fileURLToPath(
  new URL("../public/icons/maskable-source.svg", import.meta.url),
);
const outputDirectory = new URL("../public/icons/", import.meta.url);

const standardSizes = [16, 32, 48, 72, 96, 128, 144, 152, 167, 180, 192, 256, 384, 512];
const maskableSizes = [192, 512];

await Promise.all([
  ...standardSizes.map((size) =>
    sharp(standardSource)
      .resize(size, size)
      .png()
      .toFile(fileURLToPath(new URL(`icon-${size}.png`, outputDirectory))),
  ),
  ...maskableSizes.map((size) =>
    sharp(maskableSource)
      .resize(size, size)
      .png()
      .toFile(fileURLToPath(new URL(`maskable-${size}.png`, outputDirectory))),
  ),
]);
