# Dependencies

Every third-party component Registrar ships or builds on, pinned to an exact
version and hash. Nothing is installed without an entry here. The Python app
itself still needs only the Python 3 standard library.

## Self-host (Docker)

| Component | Version | Used by | Source | Hash | Why |
|---|---|---|---|---|---|
| `python` Docker base image | `3.13.16-slim` | `Dockerfile` (self-host) | https://hub.docker.com/_/python | `sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f` (image index digest) | a minimal official Python runtime for the one-command Docker self-host |

## Browser app (`webapp/`, built into `site/app/`)

Vendored into `webapp/src/vendor/` by `webapp/tools/vendor.mjs` and served from the app's own origin: **no CDN at runtime**. `webapp/tools/build.mjs` re-hashes every vendored file against `webapp/src/vendor/MANIFEST.json` and refuses to build on a mismatch. The SRI value is given for anyone who loads a file with `integrity=`; the app loads them same-origin.

### Packages (exact versions, pinned in `webapp/package.json` and `webapp/package-lock.json`)

| Package | Version | Source | Package integrity (npm) | Why |
|---|---|---|---|---|
| `pdfjs-dist` | 6.4.299 | https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.4.299.tgz | `sha512-AVl138zALtfaAPvADulE0PZThbYzCBS79nL4pOSL/6Sm/4AH5A21BD9VHt97OlCuzJuCpmeZtAtkinisF4Vb1g==` | reads the text layer of PDF certificates, and renders image-only PDF pages for OCR (legacy build: runs on current browsers) |
| `tesseract.js` | 7.0.0 | https://registry.npmjs.org/tesseract.js/-/tesseract.js-7.0.0.tgz | `sha512-exPBkd+z+wM1BuMkx/Bjv43OeLBxhL5kKWsz/9JY+DXcXdiBjiAch0V49QR3oAJqCaL5qURE0vx9Eo+G5YE7mA==` | OCR of photos and image-only PDFs, entirely in the browser; lazy-loaded |
| `tesseract.js-core` | 7.0.0 | https://registry.npmjs.org/tesseract.js-core/-/tesseract.js-core-7.0.0.tgz | `sha512-WnNH518NzmbSq9zgTPeoF8c+xmilS8rFIl1YKbk/ptuuc7p6cLNELNuPAzcmsYw450ca6bLa8j3t0VAtq435Vw==` | the Tesseract OCR engine compiled to WebAssembly (LSTM-only variants: relaxed-SIMD, SIMD, and plain fallback) |
| `@tesseract.js-data/eng` | 1.0.0 | https://registry.npmjs.org/@tesseract.js-data/eng/-/eng-1.0.0.tgz | `sha512-mbTumm6KQPUHyzTPQaF3ObXYnx0SqqfV2nabqFVQBwD6Kl7PhGSLSzOlfFTWy0P3BjghaSKA2W9GB19Jk+ZcTg==` | English recognition model for Tesseract (4.0.0 best_int, about 3 MB gzipped) |

### Shipped files

| File | From | Bytes | SHA-256 | SRI |
|---|---|---|---|---|
| `vendor/pdfjs/pdf.min.mjs` | `pdfjs-dist@6.4.299` `legacy/build/pdf.min.mjs` | 523774 | `bccc24ea711db8e44503629519904a5292d73b9daaa214bbe7cdcc282b0f4259` | `sha384-oPqHr8MeYfJpkh4zgHbWYiuwabTzthRiOS5tSWsWGmCVWlwqIkS9coGro5aa48OF` |
| `vendor/pdfjs/pdf.worker.min.mjs` | `pdfjs-dist@6.4.299` `legacy/build/pdf.worker.min.mjs` | 1321307 | `145d2dd3ab0c86151011dba95acfa2d5336e2accd59388ea43dbee0efddaaec6` | `sha384-tINN5IYq7dFaoHBUBBc2yfxGwR38mZn9GhaX31UofKYNgAg/3y8A7IjUI3GuEg/8` |
| `vendor/pdfjs/LICENSE` | `pdfjs-dist@6.4.299` `LICENSE` | 10174 | `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594` | `sha384-dIfJx5IERVwWkDwmUITjq6FFmOOiUluv24tT2o6vrjEpXyjYGE/BIj5rw25rSNr0` |
| `vendor/tesseract/tesseract.min.js` | `tesseract.js@7.0.0` `dist/tesseract.min.js` | 62961 | `000c27d9cd0def655f77b36c72a389c0ab13793aa31cb4d7aab56d09c0afbc7e` | `sha384-2BQ3U3OdKOb0Uczxqr41I9UvZkzr4V9Hv8uSzMMZAlmhsFClvdZX5wi5fDCzG+tM` |
| `vendor/tesseract/worker.min.js` | `tesseract.js@7.0.0` `dist/worker.min.js` | 111307 | `576b7df7e3393e137e51849357c9adb53fe7ac1bb69bfa06cf3d61520f182c6d` | `sha384-iUyp1FxLBc4DYaSwxT1/G6elMdSh3vvQffNSmMiySoXDpk2XfS9ZcM4RjPSiqiw3` |
| `vendor/tesseract/LICENSE.md` | `tesseract.js@7.0.0` `LICENSE.md` | 11357 | `b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1` | `sha384-blLHPbhEzMhbFqhLL0ObAPojW2VbmJiRcofyz7TeJrcNgV73VVTAmRA4szJFu+B5` |
| `vendor/tesseract/core/tesseract-core-relaxedsimd-lstm.wasm.js` | `tesseract.js-core@7.0.0` `tesseract-core-relaxedsimd-lstm.wasm.js` | 3905767 | `861a536cf9ef8e63cb644d57bab39c388f37f7d6b6f60024b741c5f6b39a59b3` | `sha384-/8lT8Rpy0sk4iWEyUA0rKewXOiWu/nV0JjVCd2vMw2nlpqBsk1/6GttRwex7g/S8` |
| `vendor/tesseract/core/tesseract-core-simd-lstm.wasm.js` | `tesseract.js-core@7.0.0` `tesseract-core-simd-lstm.wasm.js` | 3899472 | `c58b46a4c796c0b8afccf77591d5b875b6896b45d402bbce8caa6f5362447b38` | `sha384-1PHRxr8cs/w6IDh6HZYHEHS+Li9cfjahWYKnioD1xvjs7wZD20qpwhD2+ZvhDmHU` |
| `vendor/tesseract/core/tesseract-core-lstm.wasm.js` | `tesseract.js-core@7.0.0` `tesseract-core-lstm.wasm.js` | 3896484 | `eef5f8b2f8e20e150680b20adaec4a60babafee3adbe8a94583c81fee46e8680` | `sha384-ljppwjVnA7rpAU/v9enQiR6pXDStaEAYw9I+7ddiEynJcmDNnjHCmcvizBeO3cSA` |
| `vendor/tesseract/core/LICENSE` | `tesseract.js-core@7.0.0` `LICENSE` | 11358 | `c6596eb7be8581c18be736c846fb9173b69eccf6ef94c5135893ec56bd92ba08` | `sha384-HzQXqscuy26vWaM3g6T9a2KCggp/Ln7XUf8hlV9wrRqAGOYtROlr9P0JP+MpNNCc` |
| `vendor/tesseract/lang/eng.traineddata.gz` | `@tesseract.js-data/eng@1.0.0` `4.0.0_best_int/eng.traineddata.gz` | 2952873 | `45b4cb346724ac1774f1c36f42f182b887bcdb28ebe63e6fff90ac41f3fcff91` | `sha384-JI+fraGAoc5GBGIliuqzHRnP1nJyrukg5ggNSBv/TO+YOVj+6Te6XXQOx7ia10xq` |

### Development-only packages (not shipped)

Installed by `npm ci --ignore-scripts` in `webapp/` for the build and tests; nothing below reaches `site/app/` except the files listed above. Install scripts are not run. `pdfjs-dist` pulls in `@napi-rs/canvas` as an optional dependency for Node; only the platform package that matches the machine is installed.

| Package | Version | Source | Integrity (npm lockfile) |
|---|---|---|---|
| `@napi-rs/canvas` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas/-/canvas-1.0.13.tgz | `sha512-b87ugZpQ5ViwqVBkjervxpwa0GmJPsKHLBNoZN6GpjWeHzTbDUGryriq+2DYW2XbB+xCqSIINQysC0/I87nPbw==` |
| `@napi-rs/canvas-android-arm64` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-android-arm64/-/canvas-android-arm64-1.0.13.tgz | `sha512-trn7AaDzQ+yeeA0ezhkC7cB1ZDPtp4UYk0EvB5F8qtYL9jEVWtVrU30WOpI52DszoTxaTsCgf5PP4ZVaOlOIHQ==` |
| `@napi-rs/canvas-darwin-arm64` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-darwin-arm64/-/canvas-darwin-arm64-1.0.13.tgz | `sha512-6Y2djEetL8copxfNK6hg7oig/xqTwOchv19hBOm+zBNdKc/02Z3kjZN5wrLzv/l5O04/bAQS6I/AlQpwBlXgPg==` |
| `@napi-rs/canvas-darwin-x64` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-darwin-x64/-/canvas-darwin-x64-1.0.13.tgz | `sha512-OZoPwwu4TMMg8GTCFBrg8UFExwQTU/55i3uTC/izDat6PHE+ro9dimyQoOboFf91+NY7Pnn70ScR8f8I8+eZXw==` |
| `@napi-rs/canvas-linux-arm-gnueabihf` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-arm-gnueabihf/-/canvas-linux-arm-gnueabihf-1.0.13.tgz | `sha512-dZSYaKROuswU2eUWG5aBKndNVe68elo1YX93xtRax7q658tj8rYE+HwduovWJgs6qwv1r1/CLR60nDeBbyjvyg==` |
| `@napi-rs/canvas-linux-arm64-gnu` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-arm64-gnu/-/canvas-linux-arm64-gnu-1.0.13.tgz | `sha512-W6FzYE8ADHI93In1vi80KBL6btDtAMuj50Ch5ptkWS8xuntyzbR4h5JDUmXoakxZn2yMInksudTEqVSWoFqFGg==` |
| `@napi-rs/canvas-linux-arm64-musl` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-arm64-musl/-/canvas-linux-arm64-musl-1.0.13.tgz | `sha512-+6fQ+rR49hkAa4voh2stLIwQ+4t+gmoseM8Bc53/DlMXb73IKymDPWK9A/NtWeqTW4ijWppYuyuxS2hPI8tzNA==` |
| `@napi-rs/canvas-linux-riscv64-gnu` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-riscv64-gnu/-/canvas-linux-riscv64-gnu-1.0.13.tgz | `sha512-17XEMO3t9RW/TtHtSFf2AkaSWuCWdzbPeidz7zrOFawHxoLSJCnpTI1hN5aUvrZNnkNNmt/vZVJCh0ryUaik3A==` |
| `@napi-rs/canvas-linux-x64-gnu` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-x64-gnu/-/canvas-linux-x64-gnu-1.0.13.tgz | `sha512-lc48IwknVHBsNzXn9Xs5OkUsRX38Ml3Z1aOybQ51mMEzZckm8+4A0qROlyYHEoBPzr1kL3IU8ZUAqDb7wtdX8w==` |
| `@napi-rs/canvas-linux-x64-musl` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-linux-x64-musl/-/canvas-linux-x64-musl-1.0.13.tgz | `sha512-bGY1r26S2bs8UNFJcPWcCSNdCFbKep5PATsvtgtC9A5u6SRgr2/jJE/RlhHL5m9ZpixNDhVkLY/5h16Kx5V9YA==` |
| `@napi-rs/canvas-win32-arm64-msvc` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-win32-arm64-msvc/-/canvas-win32-arm64-msvc-1.0.13.tgz | `sha512-2vRPBZoaIvJrUkDhZTE2Hn0YrnpJRCWVM3EJACoBKjuWI6WgPxGtUoRMsxRviCsyks8bKTSFdyw14CellnG2qA==` |
| `@napi-rs/canvas-win32-x64-msvc` | 1.0.13 (optional) | https://registry.npmjs.org/@napi-rs/canvas-win32-x64-msvc/-/canvas-win32-x64-msvc-1.0.13.tgz | `sha512-DUlW3lZPz63kYsf8mxgyyZuevhlH0T3/BGP9l+NazhB0F1+gL6D4hqZdnhZpw24DlWmTpATDcyYSZlJOi+DdfQ==` |
| `bmp-js` | 0.1.0 | https://registry.npmjs.org/bmp-js/-/bmp-js-0.1.0.tgz | `sha512-vHdS19CnY3hwiNdkaqk93DvjVLfbEcI8mys4UjuWrlX1haDmroo8o4xCzh4wD6DGV6HxRCyauwhHRqMTfERtjw==` |
| `idb-keyval` | 6.3.0 | https://registry.npmjs.org/idb-keyval/-/idb-keyval-6.3.0.tgz | `sha512-um+2dgAWmYsu615EXpWVwSmapJhON0G43t3Ka/EVaohzPQXSMqKEqeDK/oIW3Ow+BXaF2PvSc+oBTFp793A5Ow==` |
| `is-url` | 1.2.4 | https://registry.npmjs.org/is-url/-/is-url-1.2.4.tgz | `sha512-ITvGim8FhRiYe4IQ5uHSkj7pVaPDrCTkNd3yq3cV7iZAcJdHTUMPMEHcqSOy9xZ9qFenQCvi+2wjH9a1nXqHww==` |
| `node-fetch` | 2.7.0 | https://registry.npmjs.org/node-fetch/-/node-fetch-2.7.0.tgz | `sha512-c4FRfUm/dbcWZ7U+1Wq0AwCyFL+3nt2bEw05wfxSz+DWpWsitgmSgYmy2dQdWyKC1694ELPqMs/YzUSNozLt8A==` |
| `opencollective-postinstall` | 2.0.3 | https://registry.npmjs.org/opencollective-postinstall/-/opencollective-postinstall-2.0.3.tgz | `sha512-8AV/sCtuzUeTo8gQK5qDZzARrulB3egtLzFgteqB2tcT4Mw7B8Kt7JcDHmltjz6FOAHsvTevk70gZEbhM4ZS9Q==` |
| `regenerator-runtime` | 0.13.11 | https://registry.npmjs.org/regenerator-runtime/-/regenerator-runtime-0.13.11.tgz | `sha512-kY1AZVr2Ra+t+piVaJ4gxaFaReZVH40AKNo7UCX6W+dEwBo/2oZJzqfuN1qLq1oL45o56cPaTXELwrTh8Fpggg==` |
| `tr46` | 0.0.3 | https://registry.npmjs.org/tr46/-/tr46-0.0.3.tgz | `sha512-N3WMsuqV66lT30CrXNbEjx4GEwlow3v6rr4mCcv6prnfwhS01rkgyFdjPNBYd9br7LpXV1+Emh01fHnq2Gdgrw==` |
| `wasm-feature-detect` | 1.9.0 | https://registry.npmjs.org/wasm-feature-detect/-/wasm-feature-detect-1.9.0.tgz | `sha512-zonE+xlIIYtxPy++L24ow0hAD8CICb4+FgPyROd3buyXIqsJvUEDkBgfCCoXOd1Hu3DUr0GOfnPIdcGV+YpNaA==` |
| `webidl-conversions` | 3.0.1 | https://registry.npmjs.org/webidl-conversions/-/webidl-conversions-3.0.1.tgz | `sha512-2JAn3z8AR6rjK8Sm8orRC0h/bcl/DqL7tRPdGZ4I1CjdF+EaMLmYxBHyXuKL849eucPFhvBoxMsflfOb8kxaeQ==` |
| `whatwg-url` | 5.0.0 | https://registry.npmjs.org/whatwg-url/-/whatwg-url-5.0.0.tgz | `sha512-saE57nupxk6v3HY35+jzBwYa0rKSy0XR8JSxZPwgLr7ys0IBzhGviA1/TUGJLmSVqs8pb9AnvICXEuOHLprYTw==` |
| `zlibjs` | 0.3.1 | https://registry.npmjs.org/zlibjs/-/zlibjs-0.3.1.tgz | `sha512-+J9RrgTKOmlxFSDHo0pI1xM6BLVUv+o0ZT9ANtCxGkjIVCCUdx9alUF8Gm+dGLKbkkkidWIHFDZHDMpfITt4+w==` |

### Test tooling used but not installed by this project

| Tool | Version | Why |
|---|---|---|
| Playwright (Node) | 1.56.1, with its Chromium build | headless-browser checks in `webapp/e2e/verify.mjs`; used from the machine's global install (`npm root -g`), so the repo pins nothing for it. A CI job would add `playwright@1.56.1` to `devDependencies` and record its hash here first. |
| Node.js | 22 (22.22.0 here) | runs the build and tests |
| Python | 3 (3.13 here) | exports `bodies.yaml` for the app and runs the parity checks against `cpebot/` |

