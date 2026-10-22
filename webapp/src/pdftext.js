// Turn pdf.js text items into lines a person would read: items on the same
// baseline are joined left to right; a wide horizontal gap (a table column)
// becomes a tab, which the parser treats as a label/value boundary.

export function itemsToText(items) {
  const rows = [];
  for (const it of items) {
    if (!it.str || !it.str.trim()) continue;
    const x = it.transform[4];
    const y = it.transform[5];
    const h = Math.abs(it.transform[3]) || it.height || 10;
    let row = rows.find((r) => Math.abs(r.y - y) <= Math.max(2, h * 0.3));
    if (!row) {
      row = { y, items: [] };
      rows.push(row);
    }
    row.items.push({ x, w: it.width || it.str.length * h * 0.5, h, str: it.str });
  }
  rows.sort((a, b) => b.y - a.y);
  return rows
    .map((r) => {
      r.items.sort((a, b) => a.x - b.x);
      let line = "";
      let end = null;
      for (const it of r.items) {
        if (end !== null) {
          const gap = it.x - end;
          if (gap > it.h * 1.5) line += "\t";
          else if (gap > it.h * 0.15 && !line.endsWith(" ") && !it.str.startsWith(" ")) line += " ";
        }
        line += it.str;
        end = it.x + it.w;
      }
      return line.trim();
    })
    .join("\n");
}

/** Text of every page of a PDF, using a loaded pdf.js module. */
export async function pdfText(pdfjs, data, { maxPages = 10 } = {}) {
  const task = pdfjs.getDocument({ data, isEvalSupported: false, verbosity: 0 });
  const doc = await task.promise;
  const pages = [];
  try {
    for (let i = 1; i <= Math.min(doc.numPages, maxPages); i++) {
      const page = await doc.getPage(i);
      const tc = await page.getTextContent();
      pages.push(itemsToText(tc.items));
    }
  } finally {
    await task.destroy();
  }
  return pages.join("\n\n");
}
