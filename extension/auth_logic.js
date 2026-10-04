// Login helpers that do not touch the page: which data goes where, when to lock, and the password checks.
//
// Pure functions only: the same file runs in the browser (window.AuthLogic) and under Node for tests.
(function (root) {
  "use strict";

  // The doctor's saved work (draft, versions, patient tabs) lives encrypted on the local service, not in the browser.
  const WORKSPACE_KEYS = ["patient_slots", "local_draft", "draft_history"];
  const TOUCH_EVERY_MS = 60 * 1000;
  const MIN_PASSWORD = 8;
  const MAX_PASSWORD = 128;

  const isWorkspaceKey = key => WORKSPACE_KEYS.includes(key);

  // Settings are kept per doctor under their own prefix, so one doctor's choices never reach the next.
  const scopedKey = (userId, key) => `u.${userId}.${key}`;

  // "lock": no activity for the whole idle time. "touch": active since the last ping and the ping is stale.
  function idleDecision({ now, lastActivity, lastTouch, idleMs, busy }) {
    const active = busy ? now : lastActivity;
    if (now - active >= idleMs) return "lock";
    if (active > lastTouch && now - lastTouch >= TOUCH_EVERY_MS) return "touch";
    return "none";
  }

  // Same rules as the service (server/accounts.py); the service has the last word.
  function checkPasswords(username, password, confirmation) {
    if (typeof password !== "string" || password.length < MIN_PASSWORD || password.length > MAX_PASSWORD) {
      return `密码长度需要 ${MIN_PASSWORD}～${MAX_PASSWORD} 个字符`;
    }
    if (String(username || "").trim().toLowerCase() === password.toLowerCase()) return "密码不能与用户名相同";
    if (new Set(password).size < 3) return "密码过于简单";
    if (confirmation !== undefined && confirmation !== password) return "两次输入的密码不一致";
    return "";
  }

  function checkUsername(username) {
    return /^[A-Za-z0-9_\-一-鿿]{2,20}$/.test(String(username || "").trim())
      ? "" : "用户名为 2～20 个字符，可用汉字、字母、数字、下划线和短横线";
  }

  function loginErrorText(status, payload) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : "";
    if (status === 429) return detail || "尝试次数过多，请稍后再试";
    if (status === 401) return detail && detail !== "login_required" ? detail : "用户名或密码不正确";
    if (status === 0) return "无法连接本地服务";
    return detail || "登录失败";
  }

  function parseWorkspace(text) {
    try {
      const value = JSON.parse(text);
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (error) {
      return {};
    }
  }

  function hoursText(hours) {
    const value = Number(hours);
    return Number.isFinite(value) && value > 0 ? (value % 1 === 0 ? `${value}` : value.toFixed(1)) : "12";
  }

  const api = { WORKSPACE_KEYS, TOUCH_EVERY_MS, isWorkspaceKey, scopedKey, idleDecision, checkPasswords, checkUsername, loginErrorText, parseWorkspace, hoursText };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AuthLogic = api;
})(typeof self !== "undefined" ? self : this);
