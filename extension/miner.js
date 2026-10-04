// The "extract hotwords from our own cases" dialog (opened from the 热词 tab of the editor page).
//
// Cases are sent only to the local service on this computer (API_BASE), which keeps them in memory and
// returns words and counts. They are cleared when the dialog closes. Uses API_BASE and loadHotwordPacks()
// from editor.js and MinerLogic from miner_logic.js.
(function () {
  "use strict";
  const modal = document.getElementById("minerModal");
  if (!modal || typeof MinerLogic === "undefined") return;
  const $m = selector => modal.querySelector(selector);
  const el = {
    pickFolder: $m("#minerPickFolder"), pickFiles: $m("#minerPickFiles"),
    folderInput: $m("#minerFolderInput"), filesInput: $m("#minerFilesInput"),
    pickInfo: $m("#minerPickInfo"), progress: $m("#minerProgress"),
    stepScan: $m("#minerStepScan"), minCases: $m("#minerMinCases"), caseStart: $m("#minerCaseStart"),
    scanButton: $m("#minerScanButton"), scanInfo: $m("#minerScanInfo"),
    stepSelect: $m("#minerStepSelect"), query: $m("#minerQuery"), hideCommon: $m("#minerHideCommon"),
    selectShown: $m("#minerSelectShown"), clearSelection: $m("#minerClearSelection"),
    selectionInfo: $m("#minerSelectionInfo"), rows: $m("#minerRows"),
    stepBuild: $m("#minerStepBuild"), specialty: $m("#minerSpecialty"), label: $m("#minerLabel"),
    reviewer: $m("#minerReviewer"), buildButton: $m("#minerBuildButton"), buildInfo: $m("#minerBuildInfo"),
  };
  const state = { session: null, rows: [], selected: new Set(), busy: false };

  function say(node, text, level) {
    node.textContent = text || "";
    node.className = "miner-info" + (level ? " " + level : "");
  }

  async function post(path, body, options) {
    const response = await apiFetch(`${API_BASE}/hotword-miner/${path}`, Object.assign({
      method: "POST",
      headers: body instanceof FormData ? {} : { "Content-Type": "application/json" },
      body: body instanceof FormData ? body : JSON.stringify(body),
    }, options || {}));
    let payload = null;
    try { payload = await response.json(); } catch (error) { /* not JSON */ }
    if (!response.ok) throw new Error(MinerLogic.errorText(payload, `本地服务返回 ${response.status}`));
    return payload;
  }

  function setBusy(busy) {
    state.busy = busy;
    for (const node of [el.pickFolder, el.pickFiles, el.scanButton, el.buildButton]) node.disabled = busy;
  }

  function resetAll() {
    state.session = null;
    state.rows = [];
    state.selected = new Set();
    el.rows.replaceChildren();
    el.progress.hidden = true;
    for (const step of [el.stepScan, el.stepSelect, el.stepBuild]) step.hidden = true;
    say(el.pickInfo, "支持 .txt 和 .docx；一份文件按一份病例处理。");
    say(el.scanInfo, "");
    say(el.buildInfo, "");
    el.folderInput.value = "";
    el.filesInput.value = "";
  }

  async function forget() {
    const session = state.session;
    state.session = null;
    if (!session) return;
    try { await post("clear", { session }, { keepalive: true }); } catch (error) { /* it also expires on its own */ }
  }

  // ---------------------------------------------------------------- 1. send the case files
  async function sendFiles(fileList) {
    const all = Array.from(fileList);
    if (!all.length) return;
    await forget();
    resetAll();
    const sorted = MinerLogic.classifyFiles(all);
    const notes = [];
    if (sorted.legacyDoc.length) notes.push(`${sorted.legacyDoc.length} 个旧版 .doc 文件不支持，请在 Word 中另存为 .docx`);
    if (sorted.tooLarge.length) notes.push(`${sorted.tooLarge.length} 个文件过大已跳过`);
    if (!sorted.usable.length) {
      say(el.pickInfo, `没有可用的 .txt 或 .docx 文件。${notes.join("；")}`, "warn");
      return;
    }
    const batches = MinerLogic.planBatches(sorted.usable);
    setBusy(true);
    el.progress.hidden = false;
    el.progress.max = sorted.usable.length;
    el.progress.value = 0;
    let sent = 0, last = null;
    try {
      for (const batch of batches) {
        const form = new FormData();
        if (state.session) form.append("session", state.session);
        for (const file of batch) form.append("files", file, file.name);
        last = await post("upload", form);
        state.session = last.session;
        sent += batch.length;
        el.progress.value = sent;
        say(el.pickInfo, `正在发送给本机服务……${sent} / ${sorted.usable.length}`);
      }
    } catch (error) {
      say(el.pickInfo, `发送失败：${error.message}`, "warn");
      await forget();
      setBusy(false);
      return;
    }
    setBusy(false);
    el.progress.hidden = true;
    if (last.skipped_count) notes.push(`${last.skipped_count} 个文件无法读取（${last.skipped.slice(0, 3).map(item => `${item.name}：${item.reason}`).join("；")}${last.skipped_count > 3 ? "……" : ""}）`);
    const base = `已读取 ${last.files} 个文件（${MinerLogic.formatBytes(last.bytes)}），只保存在本机内存里。`;
    say(el.pickInfo, notes.length ? `${base}${notes.join("；")}` : base, notes.length ? "warn" : "ok");
    el.stepScan.hidden = last.files === 0;
  }

  // ---------------------------------------------------------------- 2. analyse
  async function scan() {
    if (!state.session) return;
    const minCases = Number(el.minCases.value);
    if (!Number.isInteger(minCases) || minCases < 3) {
      say(el.scanInfo, "门槛至少为 3：太低时，个别患者的特殊用词可能出现在词表里。", "warn");
      return;
    }
    setBusy(true);
    say(el.scanInfo, "正在分析，病例多时需要几十秒到几分钟，请不要关闭窗口……");
    try {
      const result = await post("scan", { session: state.session, min_cases: minCases, case_start: el.caseStart.value.trim() || null });
      state.rows = result.candidates;
      state.selected = new Set();
      const dropped = result.stats && result.stats.drops ? Object.values(result.stats.drops).reduce((a, b) => a + b, 0) : 0;
      say(el.scanInfo, `分析了 ${result.cases} 份病例，找到 ${result.candidates.length} 个候选词（另有 ${dropped} 个被自动剔除）。已从病例中识别并去除 ${result.names_removed} 个姓名。`, "ok");
      el.stepSelect.hidden = false;
      el.stepBuild.hidden = false;
      renderRows();
    } catch (error) {
      say(el.scanInfo, error.message, "warn");
    }
    setBusy(false);
  }

  // ---------------------------------------------------------------- 3. choose the words
  function shownRows() {
    return MinerLogic.filterRows(state.rows, { query: el.query.value, hideCommon: el.hideCommon.checked, commonFreq: 100 });
  }

  function renderRows() {
    const fragment = document.createDocumentFragment();
    for (const row of shownRows()) {
      const tr = document.createElement("tr");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.selected.has(row.word);
      box.addEventListener("change", () => {
        if (box.checked) state.selected.add(row.word); else state.selected.delete(row.word);
        updateSelectionInfo();
      });
      const cells = [null, row.word, row.cases, row.field, row.kind, row.general === "" ? "-" : row.general];
      cells.forEach((value, index) => {
        const td = document.createElement("td");
        if (index === 0) td.append(box); else td.textContent = String(value);
        tr.append(td);
      });
      fragment.append(tr);
    }
    el.rows.replaceChildren(fragment);
    updateSelectionInfo();
  }

  function updateSelectionInfo() {
    const status = MinerLogic.selectionStatus(state.selected.size);
    say(el.selectionInfo, status.text, status.level === "warn" ? "warn" : "");
  }

  // ---------------------------------------------------------------- 4. write the pack
  async function build() {
    if (!state.session || !state.selected.size) {
      say(el.buildInfo, "请先在上面的表里勾选要采用的词。", "warn");
      return;
    }
    const reviewer = el.reviewer.value.trim();
    setBusy(true);
    try {
      const result = await post("build", {
        session: state.session, words: Array.from(state.selected), specialty: el.specialty.value,
        label: el.label.value.trim() || "本院词库", reviewers: reviewer ? [reviewer] : [],
      });
      const status = result.status === "reviewed"
        ? "已登记审核人，会默认启用。"
        : "目前是草稿：在“热词”页勾选“试用尚未经医生审核的专业词库”才会使用；医生核对后，填写审核医生姓名重新生成即可正式启用。";
      say(el.buildInfo, `已生成词库 ${result.id}（${result.count} 个词）。${status}`, "ok");
      if (result.over_advice) say(el.selectionInfo, `词数超过建议的 ${MinerLogic.WORD_ADVICE} 个，建议只留最需要的。`, "warn");
      if (typeof loadHotwordPacks === "function") loadHotwordPacks();
    } catch (error) {
      say(el.buildInfo, error.message, "warn");
    }
    setBusy(false);
  }

  async function loadSpecialties() {
    try {
      const response = await apiFetch(`${API_BASE}/specialties`, { cache: "no-store" });
      const data = await response.json();
      el.specialty.replaceChildren(...data.specialties.map(item => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        option.selected = (data.default || []).includes(item.id);
        return option;
      }));
    } catch (error) {
      say(el.buildInfo, "读取专业列表失败，请确认本地服务已启动。", "warn");
    }
  }

  // ---------------------------------------------------------------- wiring
  el.pickFolder.addEventListener("click", () => el.folderInput.click());
  el.pickFiles.addEventListener("click", () => el.filesInput.click());
  el.folderInput.addEventListener("change", () => sendFiles(el.folderInput.files));
  el.filesInput.addEventListener("change", () => sendFiles(el.filesInput.files));
  el.scanButton.addEventListener("click", scan);
  el.query.addEventListener("input", renderRows);
  el.hideCommon.addEventListener("change", renderRows);
  el.selectShown.addEventListener("click", () => { shownRows().forEach(row => state.selected.add(row.word)); renderRows(); });
  el.clearSelection.addEventListener("click", () => { state.selected.clear(); renderRows(); });
  el.buildButton.addEventListener("click", build);
  $m("#closeMinerModal").addEventListener("click", () => modal.close());
  modal.addEventListener("close", () => { forget(); resetAll(); });
  window.addEventListener("pagehide", forget);

  const opener = document.getElementById("openMinerButton");
  if (opener) opener.addEventListener("click", () => {
    resetAll();
    loadSpecialties();
    modal.showModal();
  });
})();
