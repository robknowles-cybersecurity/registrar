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

/** How long OCR of one file may take before it is stopped. */
export const OCR_TIMEOUT_MS = 60000;
/** The most pixels one rendered page may have (about 25 megapixels). */
export const MAX_RENDER_PIXELS = 25e6;

/** The render scale for a page: 2.5, or less if that would pass MAX_RENDER_PIXELS. */
export function renderScale(width, height, preferred = 2.5) {
  const fit = Math.sqrt(MAX_RENDER_PIXELS / Math.max(1, width * height));
  return Math.min(preferred, fit);
}

/** Race a promise against a deadline; on timeout, run onTimeout and reject. */
export function withDeadline(promise, deadline, message, onTimeout) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => {
      Promise.resolve(onTimeout && onTimeout()).catch(() => {}).finally(() => reject(new Error(message)));
    }, Math.max(0, deadline - Date.now()));
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

// A recognize() that runs out of time is stopped by terminating the worker;
// the next OCR starts a fresh one.
async function stopOcr() {
  const p = ocrPromise;
  ocrPromise = null;
  if (p) await (await p).terminate();
}

async function ocrImage(source, onProgress, deadline) {
  const run = (async () => {
    const worker = await ocrWorker(onProgress);
    const { data } = await worker.recognize(source);
    return { text: data.text || "", confidence: data.confidence };
  })();
  return withDeadline(run, deadline, "OCR timed out", stopOcr);
}

async function renderPdfPages(lib, bytes, maxPages = 3) {
  const task = lib.getDocument({ data: bytes, isEvalSupported: false, verbosity: 0 });
  const doc = await task.promise;
  const canvases = [];
  try {
    for (let i = 1; i <= Math.min(doc.numPages, maxPages); i++) {
      const page = await doc.getPage(i);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: renderScale(base.width, base.height) });
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
export async function extractText(file, bytes, { onProgress, ocrTimeoutMs = OCR_TIMEOUT_MS } = {}) {
  const deadline = () => Date.now() + ocrTimeoutMs;
  const isPdf = bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46;
  if (isPdf) {
    const lib = await loadPdfjs();
    const text = await pdfText(lib, bytes.slice());
    if (text.replace(/\s+/g, "").length >= 25) return { text, method: "pdf-text" };
    onProgress && onProgress({ status: "no text layer: reading the page images" });
    const pages = await renderPdfPages(lib, bytes.slice());
    const parts = [];
    let conf = 0;
    const until = deadline(); // one budget for every page of the file
    for (const c of pages) {
      const r = await ocrImage(c, onProgress, until);
      parts.push(r.text);
      conf += r.confidence || 0;
    }
    return { text: parts.join("\n\n"), method: "pdf-ocr", ocrConfidence: pages.length ? conf / pages.length : 0 };
  }
  const blob = new Blob([bytes], { type: file.type || "image/jpeg" });
  const r = await ocrImage(blob, onProgress, deadline());
  return { text: r.text, method: "image-ocr", ocrConfidence: r.confidence };
}

export function ocrLoaded() {
  return ocrPromise !== null;
}
