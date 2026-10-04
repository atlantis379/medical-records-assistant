// Run with: node --test tests/js/auth_logic.test.js   (also run by tests/test_auth_page.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const A = require("../../extension/auth_logic.js");

test("the saved work goes to the service and settings stay per doctor", () => {
  assert.deepEqual(A.WORKSPACE_KEYS.sort(), ["draft_history", "local_draft", "patient_slots"]);
  assert.ok(A.isWorkspaceKey("patient_slots"));
  assert.ok(!A.isWorkspaceKey("ui_language"));
  assert.equal(A.scopedKey("abc", "ui_language"), "u.abc.ui_language");
  assert.notEqual(A.scopedKey("a", "k"), A.scopedKey("b", "k"));
});

test("an idle page locks, an active page pings the service now and then", () => {
  const base = { now: 1_000_000, idleMs: 600_000, busy: false };
  assert.equal(A.idleDecision({ ...base, lastActivity: 1_000_000 - 600_000, lastTouch: 0 }), "lock");
  assert.equal(A.idleDecision({ ...base, lastActivity: 1_000_000 - 599_000, lastTouch: 1_000_000 - 599_000 }), "none");
  assert.equal(A.idleDecision({ ...base, lastActivity: 1_000_000 - 1_000, lastTouch: 1_000_000 - 90_000 }), "touch");
  assert.equal(A.idleDecision({ ...base, lastActivity: 1_000_000 - 1_000, lastTouch: 1_000_000 - 10_000 }), "none");
  assert.equal(A.idleDecision({ ...base, lastActivity: 1_000_000 - 1_000, lastTouch: 1_000_000 - 90_000, now: 1_000_000 }), "touch");
});

test("a dictation in progress counts as activity", () => {
  const long = { now: 5_000_000, lastActivity: 0, lastTouch: 4_000_000, idleMs: 600_000 };
  assert.equal(A.idleDecision({ ...long, busy: false }), "lock");
  assert.equal(A.idleDecision({ ...long, busy: true }), "touch");
});

test("password rules match the service", () => {
  assert.match(A.checkPasswords("admin1", "short"), /长度/);
  assert.match(A.checkPasswords("abcdefgh", "ABCDEFGH"), /用户名相同/);
  assert.match(A.checkPasswords("u1", "aaaaaaaaaa"), /简单/);
  assert.match(A.checkPasswords("u1", "long-enough-1", "different-1"), /不一致/);
  assert.equal(A.checkPasswords("u1", "long-enough-1", "long-enough-1"), "");
  assert.equal(A.checkPasswords("u1", "long-enough-1"), "");
  assert.match(A.checkPasswords("u1", "x".repeat(129)), /长度/);
});

test("user names", () => {
  for (const good of ["ab", "张医生", "dr_li-1"]) assert.equal(A.checkUsername(good), "", good);
  for (const bad of ["a", "has space", "x".repeat(21), "bad/name", ""]) assert.notEqual(A.checkUsername(bad), "", bad);
});

test("login errors are readable and a wrong name and a wrong password look the same", () => {
  assert.equal(A.loginErrorText(401, { detail: "用户名或密码不正确" }), "用户名或密码不正确");
  assert.equal(A.loginErrorText(401, null), "用户名或密码不正确");
  assert.equal(A.loginErrorText(429, { detail: "连续输错太多次，请 30 秒后再试" }), "连续输错太多次，请 30 秒后再试");
  assert.equal(A.loginErrorText(0, null), "无法连接本地服务");
});

test("a damaged saved work does not break the page", () => {
  assert.deepEqual(A.parseWorkspace("not json"), {});
  assert.deepEqual(A.parseWorkspace("[1,2]"), {});
  assert.deepEqual(A.parseWorkspace('{"local_draft":"x"}'), { local_draft: "x" });
  assert.equal(A.hoursText(12), "12");
  assert.equal(A.hoursText(0.5), "0.5");
  assert.equal(A.hoursText("x"), "12");
});
