// Run with: node --test tests/js   (also run by tests/test_hotword_miner.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../../extension/miner_logic.js");

const file = (name, size = 1000) => ({ name, size });

test("only .txt and .docx are sent; the others are counted, old .doc is named as unsupported", () => {
  const sorted = M.classifyFiles([file("a.txt"), file("b.DOCX"), file("c.doc"), file("d.pdf"), file("~$e.docx"), file(".hidden.txt"), file("big.txt", 61 * 1024 * 1024)]);
  assert.deepEqual(sorted.usable.map(f => f.name), ["a.txt", "b.DOCX"]);
  assert.deepEqual(sorted.legacyDoc, ["c.doc"]);
  assert.deepEqual(sorted.tooLarge, ["big.txt"]);
  assert.equal(sorted.other, 3);
});

test("batches respect the file count and the byte limit and keep every file", () => {
  const files = Array.from({ length: 250 }, (_, i) => file(`f${i}.txt`, 10));
  const batches = M.planBatches(files, { maxFiles: 100, maxBytes: 1e9 });
  assert.deepEqual(batches.map(b => b.length), [100, 100, 50]);
  const bySize = M.planBatches([file("a.txt", 6), file("b.txt", 6), file("c.txt", 6)], { maxFiles: 100, maxBytes: 10 });
  assert.deepEqual(bySize.map(b => b.length), [1, 1, 1]);
  assert.deepEqual(M.planBatches([], {}), []);
  assert.equal(M.planBatches(files).flat().length, 250);
});

test("filtering never changes which words are selected", () => {
  const rows = [{ word: "奥马珠单抗", general: 0 }, { word: "病史", general: 123 }, { word: "CT", general: "" }];
  assert.deepEqual(M.filterRows(rows, { query: "奥马" }).map(r => r.word), ["奥马珠单抗"]);
  assert.deepEqual(M.filterRows(rows, { query: "ct" }).map(r => r.word), ["CT"]);
  assert.deepEqual(M.filterRows(rows, { hideCommon: true, commonFreq: 100 }).map(r => r.word), ["奥马珠单抗", "CT"]);
  assert.equal(M.filterRows(rows, {}).length, 3);
});

test("the selection warns above the advised number of words", () => {
  assert.equal(M.selectionStatus(0).level, "info");
  assert.equal(M.selectionStatus(10).text, "已选 10 个");
  assert.equal(M.selectionStatus(M.WORD_ADVICE + 1).level, "warn");
});

test("error messages from the service are shown as they are", () => {
  assert.equal(M.errorText({ detail: "病例只有 2 份" }, "x"), "病例只有 2 份");
  assert.match(M.errorText({ detail: [{ loc: ["body"] }] }, "x"), /请求内容不正确/);
  assert.equal(M.errorText(null, "后备"), "后备");
});

test("sizes are readable", () => {
  assert.equal(M.formatBytes(500), "500 B");
  assert.equal(M.formatBytes(2048), "2 KB");
  assert.equal(M.formatBytes(3 * 1024 * 1024), "3.0 MB");
});
