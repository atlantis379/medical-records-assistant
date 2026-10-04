// Logic of the "import outside report" dialog that does not touch the page: which files are pictures, what to tell the
// doctor about the ones that are not, and how the checked text goes into the draft.
//
// Pure functions only: the same file runs in the browser (window.ReportLogic) and under Node for tests.
(function (root) {
  "use strict";

  const PICTURE = [".jpg", ".jpeg", ".png", ".bmp", ".webp"];
  const CONVERT_FIRST = { ".pdf": "PDF 暂不支持，请先把每一页转成图片（JPG 或 PNG）", ".heic": "iPhone 的 HEIC 暂不支持，请先转成 JPG", ".heif": "iPhone 的 HEIC 暂不支持，请先转成 JPG" };
  const MAX_FILE_BYTES = 25 * 1024 * 1024;

  function extensionOf(name) {
    const dot = String(name).lastIndexOf(".");
    return dot < 0 ? "" : String(name).slice(dot).toLowerCase();
  }

  // files: [{name, size}] -> pictures to send, and why the others are not sent.
  function classifyPictures(files) {
    const usable = [], refused = [];
    let ignored = 0;
    for (const file of files) {
      const name = String(file.name), ext = extensionOf(name);
      if (name.startsWith(".") || name.startsWith("~$")) { ignored += 1; continue; }
      if (CONVERT_FIRST[ext]) { refused.push({ name, reason: CONVERT_FIRST[ext] }); continue; }
      if (!PICTURE.includes(ext)) { ignored += 1; continue; }
      if (file.size > MAX_FILE_BYTES) { refused.push({ name, reason: "图片超过 25 MB" }); continue; }
      usable.push(file);
    }
    return { usable, refused, ignored };
  }

  function nextRotation(current, step) {
    return (((current + step) % 360) + 360) % 360;
  }

  // Appends the checked text to the draft's 辅助检查 section. `router` is window.FieldRouter, `defs` its field definitions.
  function addToDraft(draft, text, router, defs, label) {
    const body = String(text || "").replace(/\s+$/, "").replace(/^\s+/, "");
    if (!body) return draft;
    const heading = `${label || "外院报告"}：`;
    const field = router && defs ? defs.find(def => def.key === "auxiliary_exam") : null;
    if (field) return router.insertIntoField(draft, field, `\n${heading}\n${body}`, defs);
    return draft ? `${draft.replace(/\s+$/, "")}\n${heading}\n${body}` : `${heading}\n${body}`;
  }

  // The first occurrence of `from` is replaced; nothing happens when the doctor has already edited it away.
  function replaceFirst(text, from, to) {
    const at = text.indexOf(from);
    return at < 0 ? text : text.slice(0, at) + to + text.slice(at + from.length);
  }

  // The sheet's rows as lines of a record. Only the abnormal ones (an arrow, or something to check) when asked.
  function tableText(rows, onlyAbnormal) {
    return rows
      .filter(row => !onlyAbnormal || row.flag || row.note)
      .map(row => {
        const head = `${row.name} ${row.result}${row.flag}`.trim();
        const tail = [row.unit, row.range ? `（参考 ${row.range}）` : ""].filter(Boolean).join(" ");
        return `${head} ${tail}`.trim();
      })
      .join("\n");
  }

  function summary(result) {
    const parts = [`识别用时 ${result.seconds} 秒`];
    if (result.table) parts.push(`识别出化验表格，共 ${result.table.rows.length} 项`);
    if (result.removed) parts.push(`已去掉手机界面 ${result.removed} 行`);
    if (result.low_confidence) parts.push(`${result.low_confidence} 行把握较低`);
    return parts.join("，");
  }

  const api = { PICTURE, classifyPictures, nextRotation, addToDraft, replaceFirst, tableText, summary, extensionOf };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReportLogic = api;
})(typeof self !== "undefined" ? self : this);
