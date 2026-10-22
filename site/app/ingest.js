// Evidence ingest, entirely in the browser:
//   text PDF          -> pdf.js text layer
//   image-only PDF    -> pdf.js renders each page -> tesseract.js OCR
//   photo (PNG/JPEG)  -> tesseract.js OCR
// tesseract.js (about 7 MB with its English model) loads only the first time
// an image needs reading. Both libraries are vendored under vendor/; nothing
// is fetched from anywhere else.
import { pdfText } from "./pdftext.js";

const BASE = new URL("./vendor/", import.meta.url).href;
let pdfjsPromise = null;
let ocrPromise = null;

export function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(`${BASE}pdfjs/pdf.min.mjs`).then((lib) => {
      lib.GlobalWorkerOptions.workerSrc = `${BASE}pdfjs/pdf.worker.min.mjs`;
      return lib;
    });
  }
  return pdfjsPromise;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(s);
  });
}

async function ocrWorker(onProgress) {
  if (!ocrPromise) {
    ocrPromise = (async () => {
      if (!window.Tesseract) await loadScript(`${BASE}tesseract/tesseract.min.js`);
      return window.Tesseract.createWorker("eng", 1, {
        workerPath: `${BASE}tesseract/worker.min.js`,
        corePath: `${BASE}tesseract/core`,
        langPath: `${BASE}tesseract/lang`,
        gzip: true,
        cacheMethod: "none",
        workerBlobURL: false,
        logger: (m) => onProgress && onProgress(m),
      });
    })().catch((err) => {
      ocrPromise = null;
      throw err;
    });
  }
  return ocrPromise;
}

async function ocrImage(source, onProgress) {
  const worker = await ocrWorker(onProgress);
  const { data } = await worker.recognize(source);
  return { text: data.text || "", confidence: data.confidence };
}

async function renderPdfPages(lib, bytes, maxPages = 3) {
  const task = lib.getDocument({ data: bytes, isEvalSupported: false, verbosity: 0 });
  const doc = await task.promise;
  const canvases = [];
  try {
    for (let i = 1; i <= Math.min(doc.numPages, maxPages); i++) {
      const page = await doc.getPage(i);
      const viewport = page.getViewport({ scale: 2.5 });
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      await page.render({ canvas, canvasContext: canvas.getContext("2d"), viewport }).promise;
      canvases.push(canvas);
    }
  } finally {
    await task.destroy();
  }
  return canvases;
}

/**
 * Read a file's text. Returns {text, method, ocrConfidence?}.
 * method: "pdf-text" | "pdf-ocr" | "image-ocr".
 */
export async function extractText(file, bytes, { onProgress } = {}) {
  const isPdf = bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46;
  if (isPdf) {
    const lib = await loadPdfjs();
    const text = await pdfText(lib, bytes.slice());
    if (text.replace(/\s+/g, "").length >= 25) return { text, method: "pdf-text" };
    onProgress && onProgress({ status: "no text layer: reading the page images" });
    const pages = await renderPdfPages(lib, bytes.slice());
    const parts = [];
    let conf = 0;
    for (const c of pages) {
      const r = await ocrImage(c, onProgress);
      parts.push(r.text);
      conf += r.confidence || 0;
    }
    return { text: parts.join("\n\n"), method: "pdf-ocr", ocrConfidence: pages.length ? conf / pages.length : 0 };
  }
  const blob = new Blob([bytes], { type: file.type || "image/jpeg" });
  const r = await ocrImage(blob, onProgress);
  return { text: r.text, method: "image-ocr", ocrConfidence: r.confidence };
}

export function ocrLoaded() {
  return ocrPromise !== null;
}
