// Login for the editor page: the login screen, the session token, the doctor's saved work, the idle lock and the
// account management dialog. Loaded before editor.js; editor.js waits for Auth.start() before it initialises.
//
// * The token lives in chrome.storage.session (memory only: it is gone when the browser closes) and goes to the
//   local service as `Authorization: Bearer`, or as the second WebSocket sub-protocol.
// * The draft, versions and patient tabs are not kept in the browser. They go to the service, which encrypts them
//   with a key that only exists while this doctor is logged in, and deletes them after a time limit.
// * Settings (language, specialty ...) stay in the browser under a per-doctor prefix.
const Auth = (function () {
  "use strict";
  const BASE = SERVICE_BASE;          // config.js
  const TOKEN_KEY = "bingli_token";
  const NOTE_KEY = "bingli_lock_note";
  const LEGACY_FLAG = "legacy_workspace_migrated";
  const L = AuthLogic;
  const state = { token: null, user: null, idleSeconds: 600, workspaceHours: 12, workspace: {}, dirty: false, expiring: false };
  const hooks = { isBusy: () => false, persistEnabled: () => true };
  let lastActivity = Date.now(), lastTouch = Date.now(), flushTimer = null, flushChain = Promise.resolve(), timer = null;
  const $ = id => document.getElementById(id);

  // ---------------------------------------------------------------- where the token is kept
  const hasChromeStorage = () => Boolean(globalThis.chrome && chrome.storage && chrome.storage.session);
  async function readToken() {
    if (hasChromeStorage()) return (await chrome.storage.session.get(TOKEN_KEY))[TOKEN_KEY] || null;
    try { return sessionStorage.getItem(TOKEN_KEY); } catch (error) { return null; }
  }
  async function saveToken(token) {
    if (hasChromeStorage()) { await (token ? chrome.storage.session.set({ [TOKEN_KEY]: token }) : chrome.storage.session.remove(TOKEN_KEY)); return; }
    try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch (error) { /* private mode */ }
  }

  // ---------------------------------------------------------------- talking to the service
  async function apiFetch(url, options) {
    const headers = new Headers((options && options.headers) || {});
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
    const response = await fetch(url, Object.assign({}, options, { headers }));
    if (response.status === 401 && state.token && !/\/auth\/(login|setup|status)$/.test(String(url))) {
      let detail = "";
      try { detail = (await response.clone().json()).detail; } catch (error) { /* not JSON */ }
      if (detail === "login_required") expired();
    }
    return response;
  }
  async function post(path, body) {
    const response = await apiFetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let payload = null;
    try { payload = await response.json(); } catch (error) { /* empty */ }
    return { ok: response.ok, status: response.status, payload };
  }

  // ---------------------------------------------------------------- the doctor's saved work
  function scheduleFlush() {
    state.dirty = true;
    clearTimeout(flushTimer);
    flushTimer = setTimeout(flushWorkspace, 1500);
  }
  function flushWorkspace() {
    clearTimeout(flushTimer);
    if (!state.dirty || !state.token) return flushChain;
    state.dirty = false;
    flushChain = flushChain.then(async () => {
      try {
        if (hooks.persistEnabled()) {
          await apiFetch(`${BASE}/workspace`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data: JSON.stringify(state.workspace) }) });
        } else {
          await apiFetch(`${BASE}/workspace/clear`, { method: "POST" });     // saving was switched off: keep nothing
        }
      } catch (error) { state.dirty = true; }
    });
    return flushChain;
  }
  async function loadWorkspace() {
    try {
      const response = await apiFetch(`${BASE}/workspace`, { cache: "no-store" });
      const data = response.ok ? await response.json() : { exists: false };
      state.workspace = data.exists ? L.parseWorkspace(data.data) : {};
    } catch (error) { state.workspace = {}; }
  }

  // ---------------------------------------------------------------- storage used by the editor
  const area = () => (globalThis.chrome && chrome.storage && chrome.storage.local) || null;
  async function rawGet(keys) {
    if (area()) return area().get(keys);
    const out = {};
    for (const key of keys) { const v = localStorage.getItem(key); if (v !== null) out[key] = JSON.parse(v); }
    return out;
  }
  async function rawSet(values) {
    if (area()) return area().set(values);
    for (const [k, v] of Object.entries(values)) localStorage.setItem(k, JSON.stringify(v));
  }
  async function rawRemove(keys) {
    if (area()) return area().remove(keys);
    for (const key of keys) localStorage.removeItem(key);
  }
  const scoped = key => L.scopedKey(state.user ? state.user.id : "anonymous", key);

  async function storageGet(keys) {
    const out = {}, settingKeys = [];
    for (const key of keys) {
      if (L.isWorkspaceKey(key)) { if (state.workspace[key] !== undefined) out[key] = state.workspace[key]; }
      else settingKeys.push(key);
    }
    if (settingKeys.length) {
      // a doctor's own value wins; before the first login of this version the old shared value is the starting point
      const stored = await rawGet(settingKeys.flatMap(key => [scoped(key), key]));
      for (const key of settingKeys) {
        const own = stored[scoped(key)];
        if (own !== undefined) out[key] = own; else if (stored[key] !== undefined) out[key] = stored[key];
      }
    }
    return out;
  }
  async function storageSet(values) {
    const settings = {};
    for (const [key, value] of Object.entries(values)) {
      if (L.isWorkspaceKey(key)) { state.workspace[key] = value; scheduleFlush(); } else settings[scoped(key)] = value;
    }
    if (Object.keys(settings).length) await rawSet(settings);
  }
  async function storageRemove(keys) {
    const settings = [];
    for (const key of keys) {
      if (L.isWorkspaceKey(key)) { delete state.workspace[key]; scheduleFlush(); } else settings.push(scoped(key));
    }
    if (settings.length) await rawRemove(settings);
  }

  // Before this version the draft and patient tabs sat unencrypted in the browser. The first doctor to log in takes
  // them over (they are moved into that doctor's encrypted saved work) and the unencrypted copy is deleted.
  async function migrateLegacy() {
    try {
      const flag = await rawGet([LEGACY_FLAG]);
      if (flag[LEGACY_FLAG]) return;
      const old = await rawGet(L.WORKSPACE_KEYS);
      let moved = false;
      for (const key of L.WORKSPACE_KEYS) {
        if (old[key] !== undefined && state.workspace[key] === undefined) { state.workspace[key] = old[key]; moved = true; }
      }
      await rawRemove(L.WORKSPACE_KEYS);
      await rawSet({ [LEGACY_FLAG]: true });
      if (moved) { state.dirty = true; await flushWorkspace(); }
    } catch (error) { /* nothing to migrate */ }
  }

  // ---------------------------------------------------------------- locking and leaving
  async function leave(note, clearWork) {
    if (state.expiring) return;
    state.expiring = true;
    try {
      if (!clearWork) await flushWorkspace();
      await post("/auth/logout", { clear_workspace: Boolean(clearWork) });
    } catch (error) { /* the service may be gone: the token is dropped anyway */ }
    await saveToken(null);
    try { if (note) sessionStorage.setItem(NOTE_KEY, note); } catch (error) { /* ignore */ }
    location.reload();
  }
  function expired() {
    if (state.expiring) return;
    state.expiring = true;
    saveToken(null).finally(() => {
      try { sessionStorage.setItem(NOTE_KEY, "登录已过期，请重新登录。"); } catch (error) { /* ignore */ }
      location.reload();
    });
  }
  const lock = () => leave("已自动锁定。保存的草稿仍在，登录后可继续。", false);

  function startTimers() {
    for (const name of ["keydown", "pointerdown", "input", "wheel", "touchstart"]) {
      window.addEventListener(name, () => { lastActivity = Date.now(); }, { passive: true, capture: true });
    }
    clearInterval(timer);
    timer = setInterval(async () => {
      const verdict = L.idleDecision({ now: Date.now(), lastActivity, lastTouch, idleMs: state.idleSeconds * 1000, busy: hooks.isBusy() });
      if (verdict === "lock") lock();
      else if (verdict === "touch") { lastTouch = Date.now(); try { await post("/auth/touch"); } catch (error) { /* try again later */ } }
    }, 15000);
    window.addEventListener("pagehide", () => {
      if (state.dirty && state.token && hooks.persistEnabled()) {
        fetch(`${BASE}/workspace`, { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
          body: JSON.stringify({ data: JSON.stringify(state.workspace) }) }).catch(() => {});
      }
    });
  }

  // ---------------------------------------------------------------- the login screen
  function overlayShow(mode) {
    document.body.classList.add("auth-locked");
    $("authOverlay").hidden = false;
    $("authLoginForm").hidden = mode !== "login";
    $("authSetupForm").hidden = mode !== "setup";
    $("authOffline").hidden = mode !== "offline";
    $("authError").textContent = "";
    $("authTitle").textContent = mode === "setup" ? "首次使用：设置管理员" : "病历助手 · 登录";
    const note = (() => { try { const v = sessionStorage.getItem(NOTE_KEY); sessionStorage.removeItem(NOTE_KEY); return v; } catch (error) { return null; } })();
    $("authNote").textContent = note || "";
    setTimeout(() => { const first = mode === "setup" ? $("setupUsername") : $("authUsername"); if (first && !$("authOverlay").hidden) first.focus(); }, 50);
  }
  const overlayHide = () => { $("authOverlay").hidden = true; document.body.classList.remove("auth-locked"); };
  const showError = text => { $("authError").textContent = text || ""; };

  function waitForCredentials(setupRequired) {
    overlayShow(setupRequired ? "setup" : "login");
    return new Promise(resolve => {
      const busy = on => { for (const b of document.querySelectorAll("#authOverlay button[type=submit]")) b.disabled = on; };
      async function submit(path, body) {
        busy(true); showError("");
        try {
          const response = await fetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
          let payload = null;
          try { payload = await response.json(); } catch (error) { /* empty */ }
          if (!response.ok) { showError(L.loginErrorText(response.status, payload)); busy(false); return; }
          state.token = payload.token; state.user = payload.user; state.idleSeconds = payload.idle_seconds || state.idleSeconds;
          await saveToken(state.token);
          $("authPassword").value = ""; $("setupPassword").value = ""; $("setupPassword2").value = "";
          resolve();
        } catch (error) { showError(L.loginErrorText(0, null)); busy(false); }
      }
      $("authLoginForm").onsubmit = event => {
        event.preventDefault();
        const username = $("authUsername").value.trim(), password = $("authPassword").value;
        if (!username || !password) { showError("请输入用户名和密码"); return; }
        submit("/auth/login", { username, password });
      };
      $("authSetupForm").onsubmit = event => {
        event.preventDefault();
        const username = $("setupUsername").value.trim(), password = $("setupPassword").value;
        const problem = L.checkUsername(username) || L.checkPasswords(username, password, $("setupPassword2").value);
        if (problem) { showError(problem); return; }
        submit("/auth/setup", { username, password, display_name: $("setupName").value.trim() });
      };
    });
  }

  async function fetchStatus() {
    const headers = state.token ? { Authorization: `Bearer ${state.token}` } : {};
    const response = await fetch(`${BASE}/auth/status`, { cache: "no-store", headers });
    if (!response.ok) throw new Error(`status ${response.status}`);
    return response.json();
  }

  // ---------------------------------------------------------------- the user bar and the dialogs
  function showUserBar() {
    const user = state.user || { username: "", role: "doctor" };
    $("userName").textContent = `${user.display_name || user.username}${user.role === "admin" ? "（管理员）" : ""}`;
    $("userBar").hidden = false;
    $("accountsButton").hidden = user.role !== "admin";
    const miner = $("openMinerButton");
    if (miner) miner.hidden = user.role !== "admin";
    const label = $("autosaveLabelText");
    if (label) label.textContent = `自动保存草稿（加密保存在本机，${L.hoursText(state.workspaceHours)} 小时后自动删除）`;
  }

  function wireUserBar() {
    $("lockButton").onclick = () => lock();
    $("logoutButton").onclick = () => {
      if (confirm("退出登录会清除您保存的草稿、版本和患者标签（已复制到病历系统的内容不受影响）。确定退出吗？")) leave("已退出登录。", true);
    };
    $("changePasswordButton").onclick = () => { $("passwordError").textContent = ""; $("passwordForm").reset(); $("passwordModal").showModal(); };
    $("closePasswordModal").onclick = () => $("passwordModal").close();
    $("passwordForm").onsubmit = async event => {
      event.preventDefault();
      const oldPassword = $("oldPassword").value, newPassword = $("newPassword").value;
      const problem = L.checkPasswords(state.user.username, newPassword, $("newPassword2").value);
      if (problem) { $("passwordError").textContent = problem; return; }
      const result = await post("/auth/password", { old_password: oldPassword, new_password: newPassword });
      if (!result.ok) { $("passwordError").textContent = L.loginErrorText(result.status, result.payload); return; }
      $("passwordModal").close();
      alert("密码已修改。");
    };
    $("accountsButton").onclick = openAccounts;
    $("closeAccountsModal").onclick = () => $("accountsModal").close();
  }

  // ---------------------------------------------------------------- account management (administrators)
  const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
  const roleName = role => (role === "admin" ? "管理员" : "医生");

  async function openAccounts() {
    $("accountsError").textContent = "";
    $("accountsModal").showModal();
    await refreshAccounts();
  }
  async function refreshAccounts() {
    const response = await apiFetch(`${BASE}/auth/users`, { cache: "no-store" });
    if (!response.ok) { $("accountsError").textContent = "读取账号失败"; return; }
    const { users } = await response.json();
    const body = $("accountRows");
    body.replaceChildren(...users.map(user => accountRow(user)));
    const audit = await apiFetch(`${BASE}/auth/audit?limit=30`, { cache: "no-store" });
    if (audit.ok) {
      const items = (await audit.json()).items;
      $("auditList").replaceChildren(...items.map(item => text("li", `${item.at}  ${item.user || "-"}  ${item.event}${item.ok ? "" : "（失败）"}`)));
    }
  }
  function accountRow(user) {
    const row = document.createElement("tr");
    const actions = document.createElement("td");
    const button = (label, handler) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.onclick = handler; actions.append(b); return b; };
    const change = async (body, failure) => {
      const result = await post(`/auth/users/${user.id}/update`, body);
      if (!result.ok) $("accountsError").textContent = L.loginErrorText(result.status, result.payload) || failure; else { $("accountsError").textContent = ""; await refreshAccounts(); }
    };
    button("重置密码", () => resetRow(row, user));
    button(user.disabled ? "启用" : "停用", () => change({ disabled: !user.disabled }));
    button(user.role === "admin" ? "改为医生" : "设为管理员", () => change({ role: user.role === "admin" ? "doctor" : "admin" }));
    button("删除", async () => {
      if (!confirm(`确定删除账号“${user.username}”吗？该医生保存的草稿会一并删除。`)) return;
      const result = await post(`/auth/users/${user.id}/delete`);
      if (!result.ok) $("accountsError").textContent = L.loginErrorText(result.status, result.payload); else { $("accountsError").textContent = ""; await refreshAccounts(); }
    });
    row.append(text("td", user.username), text("td", user.display_name || "-"), text("td", roleName(user.role)), text("td", user.disabled ? "已停用" : "正常"), actions);
    return row;
  }
  function resetRow(after, user) {
    document.querySelectorAll(".reset-row").forEach(node => node.remove());
    const row = document.createElement("tr"), cell = document.createElement("td");
    row.className = "reset-row"; cell.colSpan = 5;
    const input = document.createElement("input"); input.type = "password"; input.placeholder = `为“${user.username}”设置新密码（至少 8 位）`; input.autocomplete = "new-password";
    const ok = document.createElement("button"); ok.type = "button"; ok.textContent = "确定";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "取消"; cancel.onclick = () => row.remove();
    ok.onclick = async () => {
      const problem = L.checkPasswords(user.username, input.value);
      if (problem) { $("accountsError").textContent = problem; return; }
      if (!confirm("重置后，该医生此前保存的草稿将无法恢复并被删除。继续吗？")) return;
      const result = await post(`/auth/users/${user.id}/reset-password`, { password: input.value });
      $("accountsError").textContent = result.ok ? "" : L.loginErrorText(result.status, result.payload);
      if (result.ok) { row.remove(); alert("密码已重置，请把新密码告诉该医生，并提醒其登录后修改。"); }
    };
    cell.append(input, ok, cancel); row.append(cell); after.after(row); input.focus();
  }
  function wireAddUser() {
    $("addUserForm").onsubmit = async event => {
      event.preventDefault();
      const username = $("newUserName").value.trim(), password = $("newUserPassword").value;
      const problem = L.checkUsername(username) || L.checkPasswords(username, password);
      if (problem) { $("accountsError").textContent = problem; return; }
      const result = await post("/auth/users", { username, password, display_name: $("newUserDisplay").value.trim(), role: $("newUserRole").value });
      if (!result.ok) { $("accountsError").textContent = L.loginErrorText(result.status, result.payload); return; }
      $("addUserForm").reset(); $("accountsError").textContent = "";
      await refreshAccounts();
    };
  }

  // ---------------------------------------------------------------- start
  async function start() {
    state.token = await readToken();
    let status = null;
    for (;;) {
      try { status = await fetchStatus(); break; } catch (error) {
        overlayShow("offline");
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
    }
    state.idleSeconds = status.idle_seconds || state.idleSeconds;
    state.workspaceHours = status.workspace_hours || state.workspaceHours;
    if (status.authenticated && status.user) {
      state.user = status.user;                                   // the token from this browser session is still good
    } else if (!status.auth_enabled) {
      state.user = { id: "0".repeat(32), username: "dev", display_name: "", role: "admin" };
    } else {
      state.token = null;
      await saveToken(null);
      await waitForCredentials(status.setup_required);
      state.workspaceHours = (await fetchStatus().catch(() => status)).workspace_hours || state.workspaceHours;
    }
    await loadWorkspace();
    await migrateLegacy();
    wireUserBar(); wireAddUser(); showUserBar(); startTimers();
    lastActivity = lastTouch = Date.now();
    overlayHide();
    return state.user;
  }

  return {
    start, apiFetch, storageGet, storageSet, storageRemove, flushWorkspace,
    configure(options) { Object.assign(hooks, options); },
    get user() { return state.user; },
    get token() { return state.token; },
    get workspaceHours() { return state.workspaceHours; },
    // the WebSocket carries the token as its second sub-protocol (a URL would end up in the service log)
    socketProtocols() { return state.token ? ["bingli.v1", `token.${state.token}`] : undefined; },
    touchWorkspace() { scheduleFlush(); },        // save again (or delete the saved copy when saving was switched off)
  };
})();

function apiFetch(url, options) { return Auth.apiFetch(url, options); }
