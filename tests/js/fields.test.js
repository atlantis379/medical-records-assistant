// Run with: node --test tests/js   (also run by tests/test_fields_js.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../../extension/fields.js");

const infectious = F.activeDefs(["infectious_disease"]);
const keys = segments => segments.map(s => [s.key, s.text]);

test("field sets follow the specialty", () => {
  const labels = specialties => F.activeDefs(specialties).map(d => d.label);
  assert.ok(labels(["orthopedics"]).includes("专科检查"));
  assert.ok(!labels(["orthopedics"]).includes("流行病学史"));
  assert.ok(labels(["infectious_disease"]).includes("流行病学史"));
  assert.ok(!labels(["infectious_disease"]).includes("专科检查"));
  assert.ok(labels([]).includes("主诉") && labels(null).includes("现病史"));
  const orders = F.activeDefs(["orthopedics", "infectious_disease"]).map(d => d.order);
  assert.deepEqual(orders, [...orders].sort((a, b) => a - b));
});

test("field names are commands only at the start or after a pause", () => {
  const defs = infectious;
  assert.equal(F.findCommands("患者主诉咳嗽", defs).length, 0);          // inside a sentence
  assert.equal(F.findCommands("主诉咳嗽", defs).length, 1);               // at the start
  assert.equal(F.findCommands("咳嗽三年，既往史高血压", defs).length, 1);   // after a pause comma
  assert.equal(F.findCommands("咳嗽三年。现病史三天", defs).length, 1);
  assert.equal(F.findCommands("体温正常\n查体，心率80", defs).length, 1);
});

test("common words are weak aliases and need punctuation", () => {
  assert.equal(F.findCommands("诊断为肺炎", infectious).length, 0);
  assert.equal(F.findCommands("诊断，肺炎", infectious).length, 1);
  assert.equal(F.findCommands("初步诊断肺炎", infectious).length, 1);
});

test("a field name followed by punctuation or a colon is removed from the content", () => {
  for (const text of ["主诉：咳嗽", "主诉，咳嗽", "主诉咳嗽", "主诉 咳嗽"]) {
    assert.deepEqual(keys(F.route(text, null, infectious).segments), [["chief_complaint", "咳嗽"]], text);
  }
});

test("routing splits by the commands and tracks the field in force", () => {
  const r = F.route("主诉，反复咳嗽三年。现病史患者三年前出现咳嗽，既往史，高血压十年", null, infectious);
  assert.deepEqual(keys(r.segments), [
    ["chief_complaint", "反复咳嗽三年。"],
    ["present_illness", "患者三年前出现咳嗽。"],   // the pause comma before 既往史 closes the sentence
    ["past_history", "高血压十年"]
  ]);
  assert.equal(r.current, "past_history");
  assert.equal(r.commandCount, 3);
});

test("text without a command goes to the current field, or stays unassigned", () => {
  assert.deepEqual(keys(F.route("咳嗽三年", "chief_complaint", infectious).segments), [["chief_complaint", "咳嗽三年"]]);
  assert.deepEqual(keys(F.route("咳嗽三年", null, infectious).segments), [[null, "咳嗽三年"]]);
});

test("text before the first command belongs to the field already in force", () => {
  const r = F.route("加重一周，现病史三天", "chief_complaint", infectious);
  assert.deepEqual(keys(r.segments), [["chief_complaint", "加重一周。"], ["present_illness", "三天"]]);
});

test("routing into an empty draft builds the record in the usual field order", () => {
  let draft = "";
  const state = { current: null };
  for (const spoken of ["既往史，高血压十年。", "主诉，反复咳嗽三年。", "现病史，患者三年前出现咳嗽。"]) {
    const result = F.apply(draft, spoken, state, infectious);
    draft = result.text;
    state.current = result.current;
  }
  assert.equal(draft, "主诉：反复咳嗽三年。\n现病史：患者三年前出现咳嗽。\n既往史：高血压十年。");
});

test("more dictation under a field is appended to that field", () => {
  let draft = "主诉：咳嗽三年。\n现病史：\n既往史：高血压。";
  draft = F.apply(draft, "患者三年前发病。", { current: "present_illness" }, infectious).text;
  draft = F.apply(draft, "夜间加重。", { current: "present_illness" }, infectious).text;
  assert.equal(draft, "主诉：咳嗽三年。\n现病史：患者三年前发病。夜间加重。\n既往史：高血压。");
});

test("template headings with a trailing newline work", () => {
  const out = F.apply("主诉：\n现病史：\n", "主诉，咳嗽。现病史，三天。", { current: null }, infectious).text;
  assert.equal(out, "主诉：咳嗽。\n现病史：三天。\n");
});

test("nothing outside the routed fields is touched", () => {
  const draft = "随手记：床号3\n主诉：咳嗽\n备注 保持原样\n既往史：无";
  const out = F.apply(draft, "现病史，三天。", { current: null }, infectious).text;
  assert.equal(out, "随手记：床号3\n主诉：咳嗽\n备注 保持原样\n现病史：三天。\n既往史：无");
});

test("dictation with no field and no command is not routed", () => {
  const result = F.apply("主诉：咳嗽", "体温36.8℃", { current: null }, infectious);
  assert.equal(result.routed, false);
  assert.equal(result.text, "主诉：咳嗽");
});

test("unassigned dictation next to a command goes above the first heading", () => {
  const out = F.apply("主诉：咳嗽", "体温36.8℃，既往史高血压", { current: null }, infectious);
  assert.equal(out.routed, true);
  assert.equal(out.text, "体温36.8℃。\n主诉：咳嗽\n既往史：高血压");
  assert.equal(F.unassignedText(out.text, infectious), "体温36.8℃。");
});

test("a field the doctor does not have is not a command", () => {
  const orthopedics = F.activeDefs(["orthopedics"]);
  assert.equal(F.findCommands("流行病学史无", orthopedics).length, 0);
  assert.equal(F.findCommands("专科检查，压痛", orthopedics).length, 1);
});

test("caret position tells which field the doctor is in", () => {
  const draft = "主诉：咳嗽\n现病史：三天";
  assert.equal(F.fieldAt(draft, 4, infectious), "chief_complaint");
  assert.equal(F.fieldAt(draft, draft.length, infectious), "present_illness");
  assert.equal(F.fieldAt("随手记", 1, infectious), null);
});

test("status, empty fields and unassigned text for the review panel", () => {
  const draft = "床号3\n主诉：咳嗽\n现病史：\n既往史：高血压";
  assert.deepEqual(F.emptyFields(draft, infectious), ["现病史"]);
  assert.equal(F.unassignedText(draft, infectious), "床号3");
  assert.equal(F.unassignedText("只是一段自由文字", infectious), "");   // no headings: nothing to flag
  const status = F.fieldStatus(draft, infectious);
  assert.deepEqual(status.chief_complaint, { present: true, empty: false });
  assert.deepEqual(status.present_illness, { present: true, empty: true });
  assert.equal(status.past_history.present, true);
  assert.equal(status.family_history, undefined);
});

test("every field has a unique key and label, and no name is claimed twice", () => {
  const defs = F.FIELD_DEFS;
  assert.equal(new Set(defs.map(d => d.key)).size, defs.length);
  assert.equal(new Set(defs.map(d => d.label)).size, defs.length);
  const everyName = defs.flatMap(d => [d.label, ...(d.aliases || []), ...(d.weak || [])]);
  assert.equal(new Set(everyName).size, everyName.length);
});
