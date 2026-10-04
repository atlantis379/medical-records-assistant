// Logic of the "extract hotwords from our own cases" dialog that does not touch the page: choosing which
// files to send, splitting them into batches, filtering the candidate table and checking the selection.
//
// Pure functions only: the same file runs in the browser (window.MinerLogic) and under Node for tests.
(function (root) {
  "use strict";

  const SUPPORTED = [".txt", ".docx"];
  const BATCH_FILES = 100;                      // the service accepts at most 200 files / 64 MB per request
  const BATCH_BYTES = 20 * 1024 * 1024;
  const MAX_FILE_BYTES = 60 * 1024 * 1024;
  const WORD_ADVICE = 300;                      // about what one recognition profile holds in total, all packs together

  function extensionOf(name) {
    const dot = String(name).lastIndexOf(".");
    return dot < 0 ? "" : String(name).slice(dot).toLowerCase();
  }

  // files: [{name, size}]. Only .txt and .docx are sent; the rest is counted so the doctor knows why.
  function classifyFiles(files) {
    const usable = [], legacyDoc = [], tooLarge = [];
    let other = 0;
    for (const file of files) {
      const name = String(file.name);
      const ext = extensionOf(name);
      if (name.startsWith("~$") || name.startsWith(".")) { other += 1; continue; }
      if (ext === ".doc") { legacyDoc.push(name); continue; }
      if (!SUPPORTED.includes(ext)) { other += 1; continue; }
      if (file.size > MAX_FILE_BYTES) { tooLarge.push(name); continue; }
      usable.push(file);
    }
    return { usable, legacyDoc, tooLarge, other };
  }

  function planBatches(files, limits) {
    const maxFiles = (limits && limits.maxFiles) || BATCH_FILES;
    const maxBytes = (limits && limits.maxBytes) || BATCH_BYTES;
    const batches = [];
    let current = [], bytes = 0;
    for (const file of files) {
      if (current.length && (current.length >= maxFiles || bytes + file.size > maxBytes)) {
        batches.push(current);
        current = [];
        bytes = 0;
      }
      current.push(file);
      bytes += file.size;
    }
    if (current.length) batches.push(current);
    return batches;
  }

  // rows: the service's candidates. `selected` is a Set of words and is not changed by filtering.
  function filterRows(rows, options) {
    const query = String((options && options.query) || "").trim().toLowerCase();
    const hideCommon = Boolean(options && options.hideCommon);
    const commonFreq = (options && options.commonFreq) || 100;
    return rows.filter(row => {
      if (query && !row.word.toLowerCase().includes(query)) return false;
      if (hideCommon && Number(row.general) >= commonFreq) return false;
      return true;
    });
  }

  function selectionStatus(count) {
    if (count === 0) return { text: "还没有勾选词", level: "info" };
    if (count > WORD_ADVICE) {
      return { text: `已选 ${count} 个，超过建议的 ${WORD_ADVICE} 个：热词总数有上限，太多会挤掉其他词库`, level: "warn" };
    }
    return { text: `已选 ${count} 个`, level: "info" };
  }

  function formatBytes(bytes) {
    if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${bytes} B`;
  }

  // The service answers errors as {"detail": "..."} (a list of objects for malformed requests).
  function errorText(payload, fallback) {
    const detail = payload && payload.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) return "请求内容不正确，请检查填写的数值";
    return fallback || "出错了";
  }

  const api = { classifyFiles, planBatches, filterRows, selectionStatus, formatBytes, errorText, WORD_ADVICE, SUPPORTED };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MinerLogic = api;
})(typeof self !== "undefined" ? self : this);
