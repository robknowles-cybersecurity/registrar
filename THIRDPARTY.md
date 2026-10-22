# Third-party code and assets

**The Python app: none.** **The browser app: four vendored, unmodified
packages, listed below.** Exact versions and per-file SHA-256 hashes are in
[docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Browser app (`webapp/src/vendor/`, shipped in `site/app/vendor/`)

| Component | Version | Licence | Files | Purpose |
|---|---|---|---|---|
| pdf.js (`pdfjs-dist`), Mozilla | 6.4.299 | Apache-2.0 (`vendor/pdfjs/LICENSE`) | `pdf.min.mjs`, `pdf.worker.min.mjs` (legacy build) | read PDF certificates in the browser |
| tesseract.js | 7.0.0 | Apache-2.0 (`vendor/tesseract/LICENSE.md`) | `tesseract.min.js`, `worker.min.js` | OCR in the browser |
| tesseract.js-core (Tesseract OCR and Leptonica compiled to WebAssembly) | 7.0.0 | Apache-2.0 (`vendor/tesseract/core/LICENSE`); Tesseract is Apache-2.0, Leptonica BSD-2-Clause | three `tesseract-core-*-lstm.wasm.js` files | the OCR engine |
| Tesseract English model (`@tesseract.js-data/eng`, from tesseract-ocr/tessdata 4.0.0 best_int) | 1.0.0 | Apache-2.0 | `vendor/tesseract/lang/eng.traineddata.gz` | English recognition |

Development-only packages used to build and test the browser app are listed
in docs/DEPENDENCIES.md; none of them is shipped.

## Python app and site

- **Runtime:** the Python 3 standard library only. Nothing is vendored.
- **PyYAML** is used *if it is already installed* and is not bundled or
  required; without it Registrar uses its own small YAML reader
  (`cpebot/_minyaml.py`, written for this project).
- **Docker self-host:** the `Dockerfile` builds on the official
  `python:3.13.16-slim` image, pinned by digest in docs/DEPENDENCIES.md. No
  third-party code is added to it.
- **Fonts:** the web apps and the ceu.tools page use the system font stack;
  no font files are shipped or fetched.
- **Icons:** `site/favicon.svg` was drawn for this project.
- **External services:** the optional Telegram capture bot talks to the
  Telegram Bot API over HTTPS, and the browser app's optional "use my own
  API key" feature talks to the model provider the person chooses. Both are
  network services the person opts into, not code included here.
- **Rules data:** `bodies.yaml` records facts about certifying bodies'
  published rules (caps, minimums, cycle lengths, fees) with links to the
  source pages, with notes paraphrasing those pages. Body and
  certification names are covered in [TRADEMARKS.md](TRADEMARKS.md).
- **Licence text:** the repository's `LICENSE` file is the
  GNU Affero General Public License v3 text, © Free Software Foundation,
  reproduced verbatim as that licence permits.

If you find something in this repository that should be listed here,
please open an issue.
