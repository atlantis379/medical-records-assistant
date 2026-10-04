// The "import outside report" dialog (opened from the 更多 menu of the editor page).
//
// Pictures of outside lab sheets and imaging reports are sent to the local service only; it keeps them in memory, reads
// the text on this computer, and returns a draft. Nothing is written into the record until the doctor has looked at the
// picture next to the text and presses the button. Uses API_BASE, apiFetch and the draft helpers of editor.js, and
// MinerLogic / ReportLogic.
(function () {
  "use strict";
  const modal = document.getElementById("reportModal");
  if (!modal || typeof ReportLogic === "undefined" || typeof MinerLogic === "undefined") return;
  const $r = selector => modal.querySelector(selector);
  const el = {
    pickFolder: $r("#reportPickFolder"), pickFiles: $r("#reportPickFiles"), folderInput: $r("#reportFolderInput"), filesInput: $r("#reportFilesInput"),
    pickInfo: $r("#reportPickInfo"), progress: $r("#reportProgress"), thumbs: $r("#reportThumbs"), review: $r("#reportReview"),
    image: $r("#reportImage"), viewer: $r("#reportViewer"), rotateLeft: $r("#reportRotateLeft"), rotateRight: $r("#reportRotateRight"),
    fit: $r("#reportFit"), status: $r("#reportStatus"), text: $r("#reportText"), suggestBox: $r("#reportSuggestBox"), suggestList: $r("#reportSuggestList"),
    checks: $r("#reportChecks"), tableBox: $r("#reportTableBox"), options: $r("#reportOptions"), count: $r("#reportCount"), tableRows: $r("#reportTableRows"), onlyAbnormal: $r("#reportOnlyAbnormal"), again: $r("#reportAgain"), write: $r("#reportWrite"), writeInfo: $r("#reportWriteInfo"),
  };
  const state = { session: null, items: [], current: null, busy: false, urls: [] };

  const say = (node, text, level) => { node.textContent = text || ""; node.className = "miner-info" + (level ? " " + level : ""); };

  async function post(path, body, options) {
    const response = await apiFetch(`${API_BASE}/report-import/${path}`, Object.assign({
      method: "POST",
      headers: body instanceof FormData ? {} : { "Content-Type": "application/json" },
      body: body instanceof FormData ? body : JSON.stringify(body),
    }, options || {}));
    let payload = null;
    try { payload = await response.json(); } catch (error) { /* not JSON */ }
    if (!response.ok) throw new Error(MinerLogic.errorText(payload, `本地服务返回 ${response.status}`));
    return payload;
  }

  function reset() {
    for (const url of state.urls) URL.revokeObjectURL(url);
    Object.assign(state, { session: null, items: [], current: null, urls: [] });
    el.thumbs.replaceChildren();
    el.review.hidden = true;
    el.progress.hidden = true;
    el.image.removeAttribute("src");
    el.text.value = "";
    el.suggestBox.hidden = true;
    el.tableBox.hidden = true;
    el.options.hidden = true;
    el.checks.textContent = "";
    say(el.pickInfo, "支持 JPG、PNG、BMP、WEBP；一次可选多张。");
    say(el.status, "");
    say(el.writeInfo, "");
    el.folderInput.value = "";
    el.filesInput.value = "";
  }

  async function forget() {
    const session = state.session;
    state.session = null;
    if (!session) return;
    try { await post("clear", { session }, { keepalive: true }); } catch (error) { /* it also expires on its own */ }
  }

  function setBusy(busy) {
    state.busy = busy;
    for (const node of [el.pickFolder, el.pickFiles, el.again, el.write]) node.disabled = busy;
  }

  // ---------------------------------------------------------------- 1. pictures
  async function sendPictures(fileList) {
    const all = Array.from(fileList);
    if (!all.length) return;
    await forget();
    reset();
    const sorted = ReportLogic.classifyPictures(all);
    const notes = sorted.refused.slice(0, 3).map(item => `${item.name}：${item.reason}`);
    if (!sorted.usable.length) {
      say(el.pickInfo, `没有可用的图片。${notes.join("；")}`, "warn");
      return;
    }
    const batches = MinerLogic.planBatches(sorted.usable, { maxFiles: 10, maxBytes: 20 * 1024 * 1024 });
    setBusy(true);
    el.progress.hidden = false;
    el.progress.max = sorted.usable.length;
    el.progress.value = 0;
    const byName = new Map();
    for (const file of sorted.usable) byName.set(file.name + "|" + file.size, file);
    try {
      for (const batch of batches) {
        const form = new FormData();
        if (state.session) form.append("session", state.session);
        for (const file of batch) form.append("files", file, file.name);
        const reply = await post("upload", form);
        state.session = reply.session;
        for (const added of reply.added) {
          const file = byName.get(added.name + "|" + added.size);
          const url = file ? URL.createObjectURL(file) : "";
          if (url) state.urls.push(url);
          state.items.push({ id: added.id, name: added.name, url, rotation: 0, result: null });
        }
        for (const refused of reply.refused) notes.push(`${refused.name}：${refused.reason}`);
        el.progress.value += batch.length;
      }
    } catch (error) {
      say(el.pickInfo, `发送失败：${error.message}`, "warn");
      await forget();
      setBusy(false);
      el.progress.hidden = true;
      return;
    }
    setBusy(false);
    el.progress.hidden = true;
    const base = `已读取 ${state.items.length} 张图片，只保存在本机内存里。`;
    say(el.pickInfo, notes.length ? `${base}${notes.slice(0, 4).join("；")}${notes.length > 4 ? "……" : ""}` : base, notes.length ? "warn" : "ok");
    renderThumbs();
    if (state.items.length) select(state.items[0]);
  }

  function renderThumbs() {
    el.thumbs.replaceChildren(...state.items.map(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "report-thumb" + (item === state.current ? " active" : "") + (item.result ? " done" : "");
      button.title = item.name;
      const picture = document.createElement("img");
      picture.src = item.url; picture.alt = "";
      const label = document.createElement("span");
      label.textContent = item.name.length > 16 ? item.name.slice(0, 7) + "…" + item.name.slice(-7) : item.name;
      button.append(picture, label);
      button.addEventListener("click", () => select(item));
      return button;
    }));
  }

  // ---------------------------------------------------------------- 2. one picture next to its text
  function showRotation(item) {
    el.image.style.transform = `rotate(${item.rotation}deg)`;
    el.viewer.classList.toggle("turned", item.rotation % 180 !== 0);
  }

  async function select(item) {
    if (state.busy) return;
    state.current = item;
    el.review.hidden = false;
    el.image.src = item.url;
    showRotation(item);
    renderThumbs();
    say(el.writeInfo, "");
    if (item.result) return show(item);
    await recognize(item);
  }

  async function recognize(item) {
    setBusy(true);
    el.text.value = "";
    el.suggestBox.hidden = true;
    el.tableBox.hidden = true;
    el.options.hidden = true;
    el.checks.textContent = "";
    say(el.status, "正在识别……（图片较大时需要几秒到十几秒）");
    try {
      item.result = await post("recognize", { session: state.session, image_id: item.id, rotate: item.rotation });
      item.edited = item.generated = item.result.text;
      item.suggestions = item.result.suggestions.slice();
    } catch (error) {
      say(el.status, error.message, "warn");
      setBusy(false);
      return;
    }
    setBusy(false);
    renderThumbs();
    show(item);
  }

  function show(item) {
    if (state.current !== item) return;
    el.text.value = item.edited;
    say(el.status, ReportLogic.summary(item.result), "ok");
    el.checks.textContent = item.result.check_items.length ? item.result.check_items.join("\n") : "（没有需要特别核对的内容）";
    const table = item.result.table;
    el.tableBox.hidden = !table;
    el.options.hidden = !table;
    if (table) renderTable(table.rows);
    renderSuggestions(item);
  }

  // Rows that will not be written (normal ones, when only the abnormal are asked for) are greyed, and the count is shown.
  function renderTable(rows) {
    const only = el.onlyAbnormal.checked;
    const written = rows.filter(row => !only || row.flag || row.note).length;
    el.count.textContent = only ? `将写入 ${written} 项（共 ${rows.length} 项）` : `将写入全部 ${rows.length} 项`;
    el.tableRows.replaceChildren(...rows.map(row => {
      const tr = document.createElement("tr");
      tr.className = (row.flag ? "flagged " : "") + (row.note ? "doubt " : "") + (only && !(row.flag || row.note) ? "skipped" : "");
      const cells = [row.name, `${row.result}${row.flag}`, row.unit, row.range, row.note];
      cells.forEach((value, index) => {
        const td = document.createElement("td");
        td.textContent = value || "";
        if (index === 4 && value) td.className = "note";
        tr.append(td);
      });
      return tr;
    }));
  }

  // The checkbox decides which rows go into the text; text the doctor has already edited is only replaced after asking.
  function regenerate() {
    const item = state.current;
    if (!item || !item.result || !item.result.table) return;
    const text = ReportLogic.tableText(item.result.table.rows, el.onlyAbnormal.checked);
    if (item.edited !== item.generated && !confirm("重新生成会替换您对文字的修改，继续吗？")) {
      el.onlyAbnormal.checked = !el.onlyAbnormal.checked;
      return;
    }
    item.edited = item.generated = el.text.value = text;
    renderTable(item.result.table.rows);
    say(el.writeInfo, "");
  }

  function renderSuggestions(item) {
    const text = el.text.value;
    item.suggestions = (item.suggestions || []).filter(s => text.includes(s.from));
    el.suggestBox.hidden = item.suggestions.length === 0;
    el.suggestList.replaceChildren(...item.suggestions.map(suggestion => {
      const row = document.createElement("div");
      row.className = "phonetic-item";
      const label = document.createElement("span");
      label.textContent = `“${suggestion.from}” 可能是：`;
      row.append(label);
      for (const option of suggestion.options) {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = option;
        button.addEventListener("click", () => {
          el.text.value = ReportLogic.replaceFirst(el.text.value, suggestion.from, option);
          item.edited = el.text.value;
          item.suggestions = item.suggestions.filter(known => known !== suggestion);
          renderSuggestions(item);
        });
        row.append(button);
      }
      const keep = document.createElement("button");
      keep.type = "button"; keep.className = "ghost"; keep.textContent = "不用改";
      keep.addEventListener("click", () => { item.suggestions = item.suggestions.filter(known => known !== suggestion); renderSuggestions(item); });
      row.append(keep);
      return row;
    }));
  }

  function turn(step) {
    const item = state.current;
    if (!item || state.busy) return;
    item.rotation = ReportLogic.nextRotation(item.rotation, step);
    showRotation(item);
    say(el.status, "图片已旋转。点“按当前方向重新识别”重新读取文字。");
  }

  // ---------------------------------------------------------------- 3. into the draft
  function write() {
    const item = state.current;
    const text = el.text.value.trim();
    if (!item || !text) { say(el.writeInfo, "没有可以写入的文字。", "warn"); return; }
    const router = window.FieldRouter;
    const defs = typeof fieldDefs === "function" ? fieldDefs() : null;
    pushUndo();
    els.draft.value = ReportLogic.addToDraft(els.draft.value, text, router, defs, "外院报告");
    updateDraftMeta();
    say(el.writeInfo, "已写入草稿的“辅助检查”，请再对照原图核对一遍数值。可点击草稿下方的“撤销”恢复。", "ok");
    setFeedback("已把外院报告的文字写入“辅助检查”。请对照原图核对。");
  }

  // ---------------------------------------------------------------- wiring
  el.pickFolder.addEventListener("click", () => el.folderInput.click());
  el.pickFiles.addEventListener("click", () => el.filesInput.click());
  el.folderInput.addEventListener("change", () => sendPictures(el.folderInput.files));
  el.filesInput.addEventListener("change", () => sendPictures(el.filesInput.files));
  el.rotateLeft.addEventListener("click", () => turn(-90));
  el.rotateRight.addEventListener("click", () => turn(90));
  el.fit.addEventListener("click", () => { el.viewer.classList.toggle("wide"); el.fit.textContent = el.viewer.classList.contains("wide") ? "缩小" : "放大"; });
  el.again.addEventListener("click", () => { if (state.current) { state.current.result = null; recognize(state.current); } });
  el.write.addEventListener("click", write);
  el.onlyAbnormal.addEventListener("change", regenerate);
  el.text.addEventListener("input", () => { if (state.current) { state.current.edited = el.text.value; renderSuggestions(state.current); } });
  $r("#closeReportModal").addEventListener("click", () => modal.close());
  modal.addEventListener("close", () => { forget(); reset(); });
  window.addEventListener("pagehide", forget);

  const opener = document.getElementById("openReportButton");
  if (opener) opener.addEventListener("click", async () => {
    reset();
    modal.showModal();
    try {
      const response = await apiFetch(`${API_BASE}/report-import/status`, { cache: "no-store" });
      const status = await response.json();
      if (!status.available) {
        for (const node of [el.pickFolder, el.pickFiles]) node.disabled = true;
        say(el.pickInfo, status.reason || "图片识别不可用。", "warn");
      } else {
        for (const node of [el.pickFolder, el.pickFiles]) node.disabled = false;
      }
    } catch (error) {
      say(el.pickInfo, "读取状态失败，请确认本地服务已启动。", "warn");
    }
  });
})();
