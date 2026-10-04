// Run with: node --test tests/js/report_logic.test.js   (also run by tests/test_report_page.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const R = require("../../extension/report_logic.js");
const F = require("../../extension/fields.js");

const file = (name, size = 1000) => ({ name, size });

test("pictures are sent, PDF and HEIC are explained, the rest is ignored", () => {
  const sorted = R.classifyPictures([file("a.JPG"), file("b.png"), file("c.pdf"), file("d.heic"), file("e.txt"), file(".hidden.jpg"), file("big.jpg", 26 * 1024 * 1024)]);
  assert.deepEqual(sorted.usable.map(f => f.name), ["a.JPG", "b.png"]);
  assert.deepEqual(sorted.refused.map(r => r.name), ["c.pdf", "d.heic", "big.jpg"]);
  assert.match(sorted.refused[0].reason, /PDF/);
  assert.match(sorted.refused[1].reason, /JPG/);
  assert.equal(sorted.ignored, 2);
});

test("rotation wraps around in steps of 90 degrees", () => {
  assert.equal(R.nextRotation(0, 90), 90);
  assert.equal(R.nextRotation(270, 90), 0);
  assert.equal(R.nextRotation(0, -90), 270);
});

test("checked text goes under 辅助检查, whatever the draft looks like", () => {
  const defs = F.activeDefs(["infectious_disease"]);
  const empty = R.addToDraft("", "双侧胸腔积液。", F, defs, "外院报告");
  assert.equal(empty, "辅助检查：\n外院报告：\n双侧胸腔积液。");
  const withField = R.addToDraft("主诉：咳嗽\n辅助检查：血常规正常\n初步诊断：肺炎", "双侧胸腔积液。", F, defs, "外院报告");
  assert.match(withField, /辅助检查：血常规正常\n外院报告：\n双侧胸腔积液。\n初步诊断：肺炎/);
  assert.ok(withField.startsWith("主诉：咳嗽\n"));
  assert.equal(R.addToDraft("主诉：咳嗽", "", F, defs), "主诉：咳嗽");
  assert.equal(R.addToDraft("随便写点", "文字", null, null, "外院报告"), "随便写点\n外院报告：\n文字");
});

test("a suggestion replaces the first occurrence only, and nothing when it is gone", () => {
  assert.equal(R.replaceFirst("辜丸积液，辜丸肿大", "辜丸", "睾丸"), "睾丸积液，辜丸肿大");
  assert.equal(R.replaceFirst("睾丸积液", "辜丸", "睾丸"), "睾丸积液");
});

test("the lab sheet's rows become record lines; only abnormal rows when asked", () => {
  const rows = [
    { name: "白细胞", result: "4.79", flag: "", unit: "10^9/L", range: "3.5-9.5", note: "" },
    { name: "红细胞", result: "6.00", flag: "↑", unit: "10^12/L", range: "4.3-5.8", note: "x" },
    { name: "有核红细胞", result: "", flag: "", unit: "/100WBC", range: "0-0", note: "没有识别出结果" },
  ];
  assert.equal(R.tableText(rows, false).split("\n").length, 3);
  assert.equal(R.tableText(rows, false).split("\n")[1], "红细胞 6.00↑ 10^12/L （参考 4.3-5.8）");
  assert.deepEqual(R.tableText(rows, true).split("\n"), ["红细胞 6.00↑ 10^12/L （参考 4.3-5.8）", "有核红细胞 /100WBC （参考 0-0）"]);
});

test("the summary mentions the table, the removed phone lines and doubtful lines", () => {
  const text = R.summary({ seconds: 4.5, table: { rows: new Array(28) }, removed: 1, low_confidence: 3 });
  assert.match(text, /4\.5 秒/);
  assert.match(text, /共 28 项/);
  assert.match(text, /手机界面 1 行/);
  assert.match(text, /3 行把握较低/);
});
