const API_BASE = "http://127.0.0.1:8765";
const STORAGE = { autosave: "autosave_enabled", draft: "local_draft", history: "draft_history", patients: "patient_slots", uiLanguage: "ui_language", dictationLanguage: "dictation_language" };
const $ = selector => document.querySelector(selector);
const els = {
  draft: $("#draft"), recordButton: $("#recordButton"), recordLabel: $("#recordLabel"), timer: $("#recordTimer"),
  statusDot: $("#statusDot"), serviceStatus: $("#serviceStatus"), serviceDetail: $("#serviceDetail"),
  templateSelect: $("#templateSelect"), insertMode: $("#insertMode"), insertTemplateButton: $("#insertTemplateButton"),
  uiLanguageSelect: $("#uiLanguageSelect"), dictationLanguageSelect: $("#dictationLanguageSelect"),
  copyButton: $("#copyButton"), preSubmitCheckButton: $("#preSubmitCheckButton"), snapshotButton: $("#snapshotButton"), exportButton: $("#exportButton"), feedbackButton: $("#feedbackButton"),
  undoButton: $("#undoButton"), clearButton: $("#clearButton"), feedback: $("#feedback"), wordCount: $("#wordCount"),
  riskList: $("#riskList"), riskCount: $("#riskCount"), autosaveToggle: $("#autosaveToggle"),
  hotwordEditor: $("#hotwordEditor"), hotwordCount: $("#hotwordCount"), saveHotwordsButton: $("#saveHotwordsButton"),
  hotwordPackList: $("#hotwordPackList"), exportHotwordsButton: $("#exportHotwordsButton"), importHotwordsButton: $("#importHotwordsButton"), importHotwordsInput: $("#importHotwordsInput"),
  historyList: $("#historyList"), clearHistoryButton: $("#clearHistoryButton"), privacyText: $("#privacyText"),
  waveCanvas: $("#waveCanvas"), silenceIndicator: $("#silenceIndicator"),
  partialDisplay: $("#partialDisplay")
};

let audioContext, mediaStream, sourceNode, processorNode, silentGain, timerHandle, autosaveTimer;
let chunks = [], recording = false, startedAt = 0, undoHistory = [], savedHistory = [];
let silenceFrames = 0, silenceMaxFrames = 35, graceFrames = 0, graceMaxFrames = 12, voicedFrames = 0;
let commaPauseFrames = 8, periodPauseFrames = 16, pauseMarkLevel = 0;
const SILENCE_THRESHOLD = 0.012;
let patientSlots = [], activeSlotId = null;
let ws = null, wsConnected = false, streamingMode = false, chunkBuffer = [];
let streamReadyPromise = null, streamFinalPromise = null, stoppingRecording = false;
const WS_URL = "ws://127.0.0.1:8765/ws/transcribe";


const I18N = {
  "zh-CN": {
    app_title: "病历助手", subtitle: "听写、核对、整理，再安全地复制到病历系统。",
    ui_language: "界面语言", dictation_language: "听写语言", section_label: "病历段落", insert_mode: "追加方式",
    insert_template: "插入结构模板", manage_templates: "管理模板", start_recording: "开始听写", stop_recording: "停止并识别",
    copy_all: "复制全文", pre_submit_check: "提交前核对", save_version: "保存版本", export_txt: "导出 TXT", beta_feedback: "内测反馈", undo: "撤销", clear: "清空",
    autosave: "在本机自动恢复草稿", draft_title: "病历草稿", review_tab: "核对", hotwords_tab: "热词", history_tab: "版本",
    review_caption: "只提示，不自动修改临床含义。", risk_empty: "识别后，剂量、频次、阴阳性和关键病原体会显示在这里。",
    voice_commands: "可口述命令", command_note: "停顿约0.7秒自动加逗号，约1.4秒自动加句号；也可口述“句号、逗号、换行”。",
    hotwords_caption: "内置词库会自动启用；下方编辑的是用户自定义热词。", save_hotwords: "保存自定义热词", export_hotwords: "导出词库", import_hotwords: "导入词库", history_caption: "手动保存的版本仅保存在本浏览器中，最多 20 个。",
    history_empty: "尚未保存版本。", clear_history: "清空版本记录", privacy_title: "隐私说明", shortcut_note: "快捷键：空格键开始/停止（编辑框未聚焦时）",
    medical_note: "本工具仅辅助录入，提交前请由医生核对。", template_modal_title: "管理病历模板", new_template: "+ 新建模板",
    template_name: "模板名称", template_prefix: "段落前缀", template_body: "模板内容", composite_template: "组合模板（插入时展开所有段落）",
    template_sections: "包含段落（逗号分隔）", save: "保存", cancel: "取消", feedback_title: "提交内测反馈", feedback_caption: "反馈默认只保存到本机服务，不会上传病历正文。请尽量不要填写患者姓名、身份证号、住院号等敏感信息。",
    feedback_category: "反馈类型", feedback_rating: "整体评分", feedback_message: "反馈内容", feedback_contact: "联系方式（选填）", diagnostics_summary: "将随反馈附带的诊断信息",
    save_feedback: "保存反馈", copy_diagnostics: "复制诊断信息", pre_submit_title: "提交前核对清单", copy_after_check: "仍要复制全文", back_to_edit: "返回修改", placeholder_draft: "点击“开始听写”，或直接在这里输入和修改病历内容……",
    autosave_on: "自动恢复已开启：草稿保存在本浏览器本地，不参与同步；录音仍不保存。", autosave_off: "默认不保存草稿和录音。只有开启自动恢复后，草稿才写入浏览器本地存储。"
  },
  "en-US": {
    app_title: "病历助手", subtitle: "Dictate, review, organize, then safely copy into the EHR.",
    ui_language: "Interface", dictation_language: "Dictation", section_label: "Clinical section", insert_mode: "Insert mode",
    insert_template: "Insert template", manage_templates: "Templates", start_recording: "Start dictation", stop_recording: "Stop & transcribe",
    copy_all: "Copy all", pre_submit_check: "Pre-submit check", save_version: "Save version", export_txt: "Export TXT", beta_feedback: "Beta feedback", undo: "Undo", clear: "Clear",
    autosave: "Restore drafts on this computer", draft_title: "Clinical draft", review_tab: "Review", hotwords_tab: "Hotwords", history_tab: "Versions",
    review_caption: "Prompts only; clinical meaning is never changed automatically.", risk_empty: "After recognition, dosage, frequency, positive/negative wording, and pathogens will appear here.",
    voice_commands: "Voice commands", command_note: "Short pauses add commas; longer pauses add periods. You may also say “period, comma, new line”.",
    hotwords_caption: "Built-in packs are enabled automatically. The editor below is for custom terms.", save_hotwords: "Save custom terms", export_hotwords: "Export packs", import_hotwords: "Import terms", history_caption: "Manual versions are stored only in this browser, up to 20.",
    history_empty: "No saved versions yet.", clear_history: "Clear versions", privacy_title: "Privacy", shortcut_note: "Shortcut: Space starts/stops when the editor is not focused.",
    medical_note: "This tool assists data entry only. Clinicians must review before submission.", template_modal_title: "Manage templates", new_template: "+ New template",
    template_name: "Template name", template_prefix: "Section prefix", template_body: "Template body", composite_template: "Composite template (expand all sections)",
    template_sections: "Included sections, separated by commas", save: "Save", cancel: "Cancel", feedback_title: "Submit beta feedback", feedback_caption: "Feedback is saved locally by default and does not upload clinical text. Avoid patient names, IDs, admission numbers, or other sensitive data.",
    feedback_category: "Category", feedback_rating: "Overall rating", feedback_message: "Feedback", feedback_contact: "Contact (optional)", diagnostics_summary: "Diagnostics included with feedback",
    save_feedback: "Save feedback", copy_diagnostics: "Copy diagnostics", pre_submit_title: "Pre-submit checklist", copy_after_check: "Copy anyway", back_to_edit: "Back to edit", placeholder_draft: "Click “Start dictation”, or type and edit the clinical note here…",
    autosave_on: "Draft restore is on: drafts are stored locally in this browser; audio is still not saved.", autosave_off: "Drafts and audio are not saved by default. Drafts are written to local browser storage only when restore is enabled."
  }
};
let uiLanguage = "zh-CN";
let dictationLanguage = "zh-CN";
function tr(key) { return (I18N[uiLanguage] && I18N[uiLanguage][key]) || I18N["zh-CN"][key] || key; }
function applyUILanguage(language) {
  uiLanguage = language === "en-US" ? "en-US" : "zh-CN";
  document.documentElement.lang = uiLanguage;
  document.querySelectorAll("[data-i18n]").forEach(node => { node.textContent = tr(node.dataset.i18n); });
  document.title = tr("app_title");
  els.draft.placeholder = tr("placeholder_draft");
  if (!recording) els.recordLabel.textContent = tr("start_recording");
  els.privacyText.textContent = els.autosaveToggle?.checked ? tr("autosave_on") : tr("autosave_off");
}
function currentDictationLanguage() { return els.dictationLanguageSelect?.value === "en-US" ? "en-US" : "zh-CN"; }
function updateLanguageSpecificUI() {
  dictationLanguage = currentDictationLanguage();
  const isEnglish = dictationLanguage === "en-US";
  els.templateSelect.disabled = isEnglish;
  els.insertTemplateButton.disabled = isEnglish;
  if (isEnglish) setFeedback(uiLanguage === "en-US" ? "English dictation is beta and currently uses batch recognition after stopping." : "英文听写为 Beta，当前会在停止后进行批量识别。中文流式不受影响。");
}

const DEFAULT_TEMPLATES = [
  { id: "builtin-1", label: "主诉", prefix: "主诉：", body: "主诉：\n", builtIn: true, order: 0 },
  { id: "builtin-2", label: "现病史", prefix: "现病史：", body: "现病史：\n患者于____前出现____，伴/不伴____。最高体温____℃。于____就诊，检查提示____，予____治疗，效果____。\n", builtIn: true, order: 1 },
  { id: "builtin-3", label: "流行病学史", prefix: "流行病学史：", body: "流行病学史：\n发病前____天内有/无____地区旅居史，有/无类似患者接触史，有/无生食史及动物接触史。\n", builtIn: true, order: 2 },
  { id: "builtin-4", label: "既往史", prefix: "既往史：", body: "既往史：\n既往____。否认/有____病史。药物及食物过敏史：____。\n", builtIn: true, order: 3 },
  { id: "builtin-5", label: "辅助检查", prefix: "辅助检查：", body: "辅助检查：\n血常规：____。炎症指标：____。病原学：____。影像学：____。\n", builtIn: true, order: 4 },
  { id: "builtin-6", label: "诊疗经过", prefix: "诊疗经过：", body: "诊疗经过：\n入院后完善____检查，考虑____，予____治疗。目前____。\n", builtIn: true, order: 5 },
  { id: "builtin-7", label: "入院记录（完整）", prefix: "", body: "", builtIn: true, isComposite: true, sections: ["主诉：", "现病史：", "流行病学史：", "既往史：", "辅助检查：", "诊疗经过："], order: 6 }
];
const STORAGE_TEMPLATES = "custom_templates";
let allTemplates = [];
let editingTemplateId = null;

const riskRules = [
  { label: "否定与结论", detail: "请核对阴阳性结论与否定表述", severity: "high", regex: /(?:[一-鿿]{1,6}(?:阴性|阳性)|未见[一-鿿]{1,8}|否认[一-鿿]{1,10}(?:病史|史)|(?:有|无)[一-鿿]{2,8}(?:史|症状|表现|接触)|考虑[一-鿿]{2,10}|排除[一-鿿]{2,10})/g },
  { label: "药物剂量", detail: "请核对数值、单位和小数点", severity: "high", regex: /\d+(?:\.\d+)?\s*(?:mg|g|μg|ug|ml|mL|IU|万U)\b/gi },
  { label: "给药频次", detail: "请核对给药间隔与频次", severity: "medium", regex: /\b(?:qd|bid|tid|qid|q\d+h|qod|qw|qn|prn|st)\b/gi },
  { label: "体温或百分比", detail: "请核对体温、血氧等数值", severity: "high", regex: /\d+(?:\.\d+)?\s*(?:℃|%)/g },
  { label: "方向与部位", detail: "请核对左、右及双侧", severity: "medium", regex: /(左侧|右侧|双侧|左肺|右肺|左上|左下|右上|右下)/g },
  { label: "重点病原体", detail: "请核对病原体名称及培养来源", severity: "medium", regex: /(肺炎克雷伯菌|鲍曼不动杆菌|铜绿假单胞菌|金黄色葡萄球菌|曲霉菌|隐球菌|结核分枝杆菌|耶氏肺孢子菌)/g }
];


const QC_RULES = [
  {
    id: "template_placeholders",
    label: "未填写模板占位符",
    labelEn: "Unfilled template placeholders",
    detail: "发现模板占位符或待补充文字，请提交前补全或删除。",
    detailEn: "Template placeholders or pending text were found. Complete or remove them before submission.",
    severity: "high",
    regex: /_{2,}|待填|待补充|待完善|未填写|请补充|TODO|TBD|\[\s*\]|【\s*】/gi,
  },
  {
    id: "drug_dose",
    label: "药物剂量/单位",
    labelEn: "Medication dose/unit",
    detail: "请核对剂量、单位、小数点和给药途径，尤其注意 mg/g、mL、IU、万U。",
    detailEn: "Verify dose, unit, decimal point, and route, especially mg/g, mL, IU.",
    severity: "high",
    regex: /\d+(?:\.\d+)?\s*(?:mg|g|μg|ug|mcg|ml|mL|L|IU|U|万U|片|粒|支|瓶|袋)\b/gi,
  },
  {
    id: "drug_frequency",
    label: "给药频次/间隔",
    labelEn: "Medication frequency/interval",
    detail: "请核对 qd、bid、tid、qid、qxh、prn、st 等频次是否与医嘱一致。",
    detailEn: "Verify qd, bid, tid, qid, qxh, prn, st and other frequencies against orders.",
    severity: "high",
    regex: /\b(?:qd|bid|tid|qid|q\d+h|qod|qw|qn|qhs|prn|st|once daily|twice daily|three times daily|every \d+ hours?)\b/gi,
  },
  {
    id: "prescription_action",
    label: "处方/医嘱动作",
    labelEn: "Prescription/order action",
    detail: "出现给药、加用、停用、调整、换用等动作，请核对是否已真实开立或变更医嘱。",
    detailEn: "Medication/order actions were found. Verify they match actual orders or prescription changes.",
    severity: "medium",
    regex: /(给予|予以|加用|联合|停用|暂停|调整|减量|增量|改为|换用|继续|出院带药|开具|处方|医嘱|prescribe[sd]?|start(?:ed)?|stop(?:ped)?|hold|switch(?:ed)?|increase[sd]?|decrease[sd]?|continue[sd]?|discharge medication)/gi,
  },
  {
    id: "must_review_negation",
    label: "阴阳性/否定词",
    labelEn: "Positive/negative wording",
    detail: "阴性、阳性、有/无、否认、未见等词会改变临床含义，请逐项核对。",
    detailEn: "Positive/negative or negation terms change clinical meaning. Verify each item.",
    severity: "high",
    regex: /(阴性|阳性|有|无|否认|未见|排除|考虑|positive|negative|denies|without|no evidence of)/gi,
  }
];
let lastPreSubmitIssueCount = 0;
let lastPreSubmitHighCount = 0;

function uniqueMatches(text, regex, limit = 12) {
  regex.lastIndex = 0;
  const values = [];
  for (const match of text.matchAll(regex)) {
    const value = String(match[0]).trim();
    if (value && !values.includes(value)) values.push(value);
    if (values.length >= limit) break;
  }
  return values;
}

function buildPreSubmitIssues(text) {
  const issues = [];
  const trimmed = text.trim();
  if (!trimmed) {
    issues.push({ id: "empty", label: "草稿为空", labelEn: "Empty draft", detail: "当前没有可提交内容。", detailEn: "There is no content to submit.", severity: "high", values: [] });
    return issues;
  }
  for (const rule of QC_RULES) {
    const values = uniqueMatches(text, rule.regex, rule.id === "must_review_negation" ? 20 : 12);
    if (values.length) issues.push({ ...rule, values });
  }
  const hasDose = issues.some(item => item.id === "drug_dose");
  const hasFreq = issues.some(item => item.id === "drug_frequency");
  const hasAction = issues.some(item => item.id === "prescription_action");
  if (hasDose && !hasFreq) {
    issues.push({ id: "dose_without_frequency", label: "有剂量但未见频次", labelEn: "Dose without frequency", detail: "发现药物剂量，但未见 qd/bid/tid/qxh 等频次。请确认是否需要补充。", detailEn: "A dose was found but no common frequency was detected. Confirm whether a frequency is needed.", severity: "medium", values: [] });
  }
  if (hasAction && !hasDose) {
    issues.push({ id: "action_without_dose", label: "有处方动作但未见剂量", labelEn: "Order action without dose", detail: "出现处方/医嘱动作，但未检测到明确剂量。请确认病历表述是否完整。", detailEn: "An order action was found without a clear dose. Verify the note is complete.", severity: "medium", values: [] });
  }
  return issues;
}

function issueTitle(issue) { return uiLanguage === "en-US" ? (issue.labelEn || issue.label) : issue.label; }
function issueDetail(issue) { return uiLanguage === "en-US" ? (issue.detailEn || issue.detail) : issue.detail; }

function renderIssueNode(issue, compact = false) {
  const node = document.createElement("div");
  node.className = `${compact ? "risk-item" : "qc-item"} severity-${issue.severity}`;
  const title = document.createElement("strong");
  title.textContent = issueTitle(issue);
  const detail = document.createElement("span");
  detail.textContent = issueDetail(issue);
  node.append(title, detail);
  if (issue.values?.length) {
    const examples = document.createElement("div");
    examples.className = "risk-examples";
    for (const value of issue.values.slice(0, compact ? 8 : 20)) {
      const code = document.createElement("code");
      code.textContent = value;
      examples.appendChild(code);
    }
    node.appendChild(examples);
  }
  return node;
}

function renderPreSubmitChecklist(openModal = false) {
  const issues = buildPreSubmitIssues(els.draft.value);
  lastPreSubmitIssueCount = issues.length;
  lastPreSubmitHighCount = issues.filter(item => item.severity === "high").length;
  const summary = document.getElementById("preSubmitSummary");
  const list = document.getElementById("preSubmitList");
  if (!summary || !list) return issues;
  summary.classList.toggle("has-high", lastPreSubmitHighCount > 0);
  if (!issues.length) {
    summary.textContent = uiLanguage === "en-US" ? "No obvious pre-submit issues found. Please still perform clinical review." : "暂未发现明显提交前风险。仍请按临床要求最终核对。";
    list.innerHTML = `<div class="qc-empty">${uiLanguage === "en-US" ? "Checklist clear." : "核对清单为空。"}</div>`;
  } else {
    summary.textContent = uiLanguage === "en-US"
      ? `Found ${issues.length} item(s), including ${lastPreSubmitHighCount} high-priority item(s). This tool only prompts; clinicians decide the final text.`
      : `发现 ${issues.length} 项需核对内容，其中 ${lastPreSubmitHighCount} 项为高优先级。工具只提示，不自动修改病历。`;
    list.replaceChildren(...issues.map(issue => renderIssueNode(issue)));
  }
  if (openModal) document.getElementById("preSubmitModal").showModal();
  return issues;
}

function updateReviewPanelFromIssues(issues) {
  const display = issues.slice(0, 8);
  els.riskCount.textContent = String(issues.length);
  if (!display.length) {
    els.riskList.innerHTML = `<div class="empty-state">${uiLanguage === "en-US" ? "No obvious review prompts yet." : "暂未发现需要特别核对的内容。"}</div>`;
    return;
  }
  els.riskList.replaceChildren(...display.map(issue => renderIssueNode(issue, true)));
}

async function copyDraftText(force = false) {
  if (!els.draft.value.trim()) return setFeedback(uiLanguage === "en-US" ? "Draft is empty." : "草稿为空，暂无内容可复制。", true);
  const issues = renderPreSubmitChecklist(false);
  const highCount = issues.filter(item => item.severity === "high").length;
  if (!force && highCount > 0) {
    renderPreSubmitChecklist(true);
    setFeedback(uiLanguage === "en-US" ? "Please review high-priority checklist items before copying." : "复制前请先核对高优先级项目。", true);
    return;
  }
  await navigator.clipboard.writeText(els.draft.value);
  setFeedback(uiLanguage === "en-US" ? "Copied. Please paste only after clinical review." : "已复制全文，可粘贴到客户端病历系统。提交前请再次核对。",
  );
}

async function storageGet(keys) {
  if (globalThis.chrome?.storage?.local) return chrome.storage.local.get(keys);
  const result = {};
  for (const key of keys) { const value = localStorage.getItem(key); if (value !== null) result[key] = JSON.parse(value); }
  return result;
}
async function storageSet(values) {
  if (globalThis.chrome?.storage?.local) return chrome.storage.local.set(values);
  for (const [key, value] of Object.entries(values)) localStorage.setItem(key, JSON.stringify(value));
}
async function storageRemove(keys) {
  if (globalThis.chrome?.storage?.local) return chrome.storage.local.remove(keys);
  for (const key of keys) localStorage.removeItem(key);
}
function setFeedback(message, error = false) { els.feedback.textContent = message; els.feedback.style.color = error ? "#a33a37" : "#667a75"; }
function pushUndo() { undoHistory.push(els.draft.value); if (undoHistory.length > 30) undoHistory.shift(); els.undoButton.disabled = false; }

function updateDraftMeta() {
  const text = els.draft.value;
  const unit = uiLanguage === "en-US" ? " chars" : " 字";
  els.wordCount.textContent = `${text.replace(/\s/g, "").length}${unit}`;
  const issues = buildPreSubmitIssues(text);
  updateReviewPanelFromIssues(issues);
  scheduleAutosave();
}

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  if (!els.autosaveToggle.checked) return;
  autosaveTimer = setTimeout(() => { saveCurrentSlotState(); persistPatientSlots(); }, 500);
}

async function checkService() {
  try {
    const response = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!response.ok) throw new Error();
    const data = await response.json();
    els.statusDot.className = "status-dot online"; els.serviceStatus.textContent = "本地服务已连接";
    els.serviceDetail.textContent = `${data.model_loaded ? "模型已加载" : "首次识别时加载"} · ${data.hotword_count ?? 0} 个热词`;
    els.recordButton.disabled = false;
  } catch {
    els.statusDot.className = "status-dot offline"; els.serviceStatus.textContent = "本地服务未启动";
    els.serviceDetail.textContent = "请运行 start_server.bat"; els.recordButton.disabled = true;
  }
}


function renderHotwordPacks(data) {
  const packs = data?.packs || [];
  if (!els.hotwordPackList) return;
  if (!packs.length) {
    els.hotwordPackList.innerHTML = `<div class="empty-state">${uiLanguage === "en-US" ? "No hotword packs found." : "未找到词库包。"}</div>`;
    return;
  }
  els.hotwordPackList.replaceChildren(...packs.map(pack => {
    const node = document.createElement("div");
    node.className = "hotword-pack";
    const title = document.createElement("strong");
    title.textContent = uiLanguage === "en-US" ? (pack.label_en || pack.label) : pack.label;
    const meta = document.createElement("span");
    meta.textContent = `${pack.count} ${uiLanguage === "en-US" ? "terms" : "个词"} · ${pack.built_in ? (uiLanguage === "en-US" ? "built-in" : "内置") : (uiLanguage === "en-US" ? "custom" : "自定义")}`;
    const badge = document.createElement("span");
    badge.className = "pack-badge";
    badge.textContent = pack.enabled ? (uiLanguage === "en-US" ? "Enabled" : "已启用") : (uiLanguage === "en-US" ? "Disabled" : "未启用");
    node.append(title, meta, badge);
    return node;
  }));
}

async function loadHotwordPacks() {
  try {
    const response = await fetch(`${API_BASE}/hotword-packs`, { cache: "no-store" });
    if (!response.ok) throw new Error("读取词库包失败");
    const data = await response.json();
    renderHotwordPacks(data);
    if (data.total_count !== undefined) els.hotwordCount.textContent = `${data.total_count} ${uiLanguage === "en-US" ? "enabled" : "已启用"}`;
  } catch (error) {
    if (els.hotwordPackList) els.hotwordPackList.innerHTML = `<div class="empty-state">${uiLanguage === "en-US" ? "Local service unavailable." : "本地服务未连接，无法读取词库包。"}</div>`;
  }
}

async function exportHotwordPacks() {
  try {
    const response = await fetch(`${API_BASE}/hotword-packs/export`, { cache: "no-store" });
    if (!response.ok) throw new Error("导出失败");
    const data = await response.json();
    const stamp = new Date().toISOString().slice(0, 19).replaceAll(":", "-");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `medical-hotword-packs-${stamp}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setFeedback(uiLanguage === "en-US" ? "Hotword packs exported." : "词库已导出。内置词库只读，导入时只写入用户自定义词库。" );
  } catch (error) {
    setFeedback(`${uiLanguage === "en-US" ? "Export failed" : "导出失败"}：${error.message}`, true);
  }
}

async function importHotwordFile(file) {
  if (!file) return;
  const text = await file.text();
  let payload;
  if (file.name.toLowerCase().endsWith(".json")) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { text };
    }
  } else {
    payload = { text };
  }
  try {
    const response = await fetch(`${API_BASE}/hotword-packs/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "导入失败");
    els.hotwordEditor.value = data.words.join("\n");
    setFeedback(uiLanguage === "en-US" ? `Imported ${data.added} new custom term(s).` : `已导入 ${data.added} 个新自定义热词。`);
    await loadHotwordPacks();
    checkService();
  } catch (error) {
    setFeedback(`${uiLanguage === "en-US" ? "Import failed" : "导入失败"}：${error.message}`, true);
  } finally {
    els.importHotwordsInput.value = "";
  }
}

async function loadHotwords() {
  try {
    const response = await fetch(`${API_BASE}/hotwords`, { cache: "no-store" });
    if (!response.ok) throw new Error("读取失败");
    const data = await response.json(); els.hotwordEditor.value = data.words.join("\n"); els.hotwordCount.textContent = `${data.count} 个`;
  } catch { els.hotwordCount.textContent = "服务未连接"; }
}
async function saveHotwords() {
  const words = els.hotwordEditor.value.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  try {
    const response = await fetch(`${API_BASE}/hotwords`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ words }) });
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "保存失败");
    els.hotwordEditor.value = data.words.join("\n"); els.hotwordCount.textContent = `${data.count} 个`;
    setFeedback(`已保存 ${data.count} 个本地热词，下一次识别生效。`); loadHotwordPacks(); checkService();
  } catch (error) { setFeedback(`热词保存失败：${error.message}`, true); }
}

function mergeText(text) {
  const prefix = els.templateSelect.value; let value = text.trim();
  if (prefix && !value.startsWith(prefix)) value = `${prefix}${value}`;
  pushUndo();
  if (els.insertMode.value === "replace") els.draft.value = value;
  else if (els.insertMode.value === "cursor") els.draft.setRangeText(value, els.draft.selectionStart, els.draft.selectionEnd, "end");
  else els.draft.value += `${els.draft.value.trim() ? "\n" : ""}${value}`;
  updateDraftMeta();
}
function insertStructuredTemplate() {
  const key = els.templateSelect.value;
  if (!key) return setFeedback("请先选择一个病历段落。", true);
  const tpl = allTemplates.find(t => t.prefix === key || t.id === key);
  if (!tpl) return setFeedback("未找到对应模板。", true);
  pushUndo();
  let value;
  if (tpl.isComposite && tpl.sections) {
    value = tpl.sections.map(sec => {
      const sub = allTemplates.find(t => t.prefix === sec);
      return sub ? sub.body : sec + "\n";
    }).join("\n");
  } else {
    value = tpl.body || tpl.prefix;
  }
  els.draft.setRangeText(`${els.draft.value && els.draft.selectionStart > 0 ? "\n" : ""}${value}`, els.draft.selectionStart, els.draft.selectionEnd, "end");
  els.draft.focus(); updateDraftMeta(); setFeedback("结构模板已插入，请补充并核对内容。");
}

async function loadTemplates() {
  const stored = await storageGet([STORAGE_TEMPLATES]);
  const custom = Array.isArray(stored[STORAGE_TEMPLATES]) ? stored[STORAGE_TEMPLATES] : [];
  allTemplates = [...DEFAULT_TEMPLATES.map(t => {
    const override = custom.find(c => c.id === t.id);
    return override ? { ...t, ...override, builtIn: true } : t;
  }), ...custom.filter(c => !c.id.startsWith("builtin-"))];
  allTemplates.sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
  rebuildTemplateDropdown();
}

function rebuildTemplateDropdown() {
  const select = els.templateSelect;
  const currentValue = select.value;
  select.innerHTML = '<option value="">自由听写</option>';
  for (const tpl of allTemplates) {
    const opt = document.createElement("option");
    opt.value = tpl.prefix || tpl.id;
    opt.textContent = tpl.label + (tpl.isComposite ? "（组合）" : "");
    select.appendChild(opt);
  }
  if ([...select.options].some(o => o.value === currentValue)) select.value = currentValue;
}

function openTemplateManager() {
  renderTemplateList();
  document.getElementById("templateManagerView").hidden = false;
  document.getElementById("templateFormView").hidden = true;
  document.getElementById("templateModal").showModal();
}

function renderTemplateList() {
  const list = document.getElementById("templateList");
  if (!allTemplates.length) { list.innerHTML = '<div class="empty-state">暂无模板。</div>'; return; }
  list.replaceChildren(...allTemplates.map(tpl => {
    const node = document.createElement("div"); node.className = "template-list-item";
    const info = document.createElement("div"); info.className = "tpl-info";
    const name = document.createElement("span"); name.className = "tpl-name"; name.textContent = tpl.label;
    const prefix = document.createElement("span"); prefix.className = "tpl-prefix"; prefix.textContent = tpl.isComposite ? `组合：${(tpl.sections || []).join(", ")}` : (tpl.prefix || "无前缀");
    info.append(name, document.createElement("br"), prefix);
    const actions = document.createElement("div"); actions.className = "tpl-actions";
    const editBtn = document.createElement("button"); editBtn.textContent = "编辑"; editBtn.addEventListener("click", () => openTemplateForm(tpl));
    actions.appendChild(editBtn);
    if (!tpl.builtIn) {
      const delBtn = document.createElement("button"); delBtn.textContent = "删除"; delBtn.className = "danger-ghost";
      delBtn.addEventListener("click", () => deleteTemplate(tpl.id));
      actions.appendChild(delBtn);
    }
    node.append(info, actions); return node;
  }));
}

function openTemplateForm(tpl = null) {
  editingTemplateId = tpl ? tpl.id : null;
  document.getElementById("tplName").value = tpl ? tpl.label : "";
  document.getElementById("tplPrefix").value = tpl ? (tpl.prefix || "") : "";
  document.getElementById("tplBody").value = tpl ? (tpl.body || "") : "";
  document.getElementById("tplComposite").checked = tpl ? !!tpl.isComposite : false;
  document.getElementById("tplSectionList").value = tpl && tpl.sections ? tpl.sections.join(",") : "";
  document.getElementById("tplSections").hidden = !(tpl && tpl.isComposite);
  document.getElementById("templateManagerView").hidden = true;
  document.getElementById("templateFormView").hidden = false;
}

async function saveTemplateForm() {
  const label = document.getElementById("tplName").value.trim();
  const prefix = document.getElementById("tplPrefix").value.trim();
  const body = document.getElementById("tplBody").value;
  const isComposite = document.getElementById("tplComposite").checked;
  const sections = isComposite ? document.getElementById("tplSectionList").value.split(/[,，]/).map(s => s.trim()).filter(Boolean) : undefined;
  if (!label) return setFeedback("请填写模板名称。", true);
  if (!isComposite && !body) return setFeedback("请填写模板内容。", true);
  const stored = await storageGet([STORAGE_TEMPLATES]);
  let custom = Array.isArray(stored[STORAGE_TEMPLATES]) ? stored[STORAGE_TEMPLATES] : [];
  if (editingTemplateId) {
    const existing = custom.find(c => c.id === editingTemplateId);
    const update = { id: editingTemplateId, label, prefix, body, isComposite, sections, order: existing?.order ?? allTemplates.length };
    if (editingTemplateId.startsWith("builtin-")) {
      custom = custom.filter(c => c.id !== editingTemplateId);
      custom.push(update);
    } else {
      custom = custom.map(c => c.id === editingTemplateId ? update : c);
    }
  } else {
    custom.push({ id: crypto.randomUUID(), label, prefix, body, isComposite, sections, order: allTemplates.length });
  }
  await storageSet({ [STORAGE_TEMPLATES]: custom });
  await loadTemplates();
  setFeedback(`模板"${label}"已保存。`);
  renderTemplateList();
  document.getElementById("templateManagerView").hidden = false;
  document.getElementById("templateFormView").hidden = true;
}

async function deleteTemplate(id) {
  const stored = await storageGet([STORAGE_TEMPLATES]);
  let custom = Array.isArray(stored[STORAGE_TEMPLATES]) ? stored[STORAGE_TEMPLATES] : [];
  custom = custom.filter(c => c.id !== id);
  await storageSet({ [STORAGE_TEMPLATES]: custom });
  await loadTemplates();
  renderTemplateList();
  setFeedback("模板已删除。");
}

function createSlot(label) {
  return { id: crypto.randomUUID(), label, draft: "", history: [], createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
}

function getActiveSlot() {
  return patientSlots.find(s => s.id === activeSlotId) || patientSlots[0];
}

function saveCurrentSlotState() {
  const slot = getActiveSlot();
  if (!slot) return;
  slot.draft = els.draft.value;
  slot.history = savedHistory;
  slot.updatedAt = new Date().toISOString();
}

async function persistPatientSlots() {
  await storageSet({ [STORAGE.patients]: { activeSlotId, slots: patientSlots } });
}

async function loadPatientSlots() {
  const stored = await storageGet([STORAGE.patients, STORAGE.draft, STORAGE.history]);
  const data = stored[STORAGE.patients];
  if (data && Array.isArray(data.slots) && data.slots.length > 0) {
    patientSlots = data.slots;
    activeSlotId = data.activeSlotId || patientSlots[0].id;
  } else {
    const legacyDraft = stored[STORAGE.draft] || "";
    const legacyHistory = Array.isArray(stored[STORAGE.history]) ? stored[STORAGE.history] : [];
    const slot = createSlot("默认患者");
    slot.draft = legacyDraft;
    slot.history = legacyHistory;
    patientSlots = [slot];
    activeSlotId = slot.id;
    await persistPatientSlots();
  }
  restoreSlot(activeSlotId);
  renderPatientTabs();
}

function restoreSlot(slotId) {
  const slot = patientSlots.find(s => s.id === slotId);
  if (!slot) return;
  activeSlotId = slot.id;
  els.draft.value = slot.draft || "";
  savedHistory = slot.history || [];
  undoHistory = [];
  els.undoButton.disabled = true;
  renderHistory();
  updateDraftMeta();
}

async function switchSlot(slotId) {
  if (slotId === activeSlotId) return;
  if (recording) { setFeedback("请先停止录音再切换患者。", true); return; }
  saveCurrentSlotState();
  restoreSlot(slotId);
  await persistPatientSlots();
  setFeedback(`已切换到：${getActiveSlot().label}`);
  renderPatientTabs();
}

async function addPatientSlot(label) {
  if (patientSlots.length >= 20) { setFeedback("最多支持 20 位患者草稿。", true); return; }
  if (!label || !label.trim()) return;
  saveCurrentSlotState();
  const slot = createSlot(label.trim());
  patientSlots.push(slot);
  activeSlotId = slot.id;
  restoreSlot(slot.id);
  await persistPatientSlots();
  renderPatientTabs();
  setFeedback(`已新增患者：${slot.label}`);
}

async function removePatientSlot(slotId) {
  if (patientSlots.length <= 1) { setFeedback("至少保留一个患者草稿。", true); return; }
  const slot = patientSlots.find(s => s.id === slotId);
  patientSlots = patientSlots.filter(s => s.id !== slotId);
  if (activeSlotId === slotId) {
    activeSlotId = patientSlots[0].id;
    restoreSlot(activeSlotId);
  }
  await persistPatientSlots();
  renderPatientTabs();
  setFeedback(`已移除：${slot?.label || "患者"}`);
}

async function renamePatientSlot(slotId, newLabel) {
  const slot = patientSlots.find(s => s.id === slotId);
  if (!slot || !newLabel.trim()) return;
  slot.label = newLabel.trim();
  await persistPatientSlots();
  renderPatientTabs();
}

function renderPatientTabs() {
  const container = document.getElementById("patientTabs");
  container.replaceChildren(...patientSlots.map(slot => {
    const tab = document.createElement("div");
    tab.className = `patient-tab${slot.id === activeSlotId ? " active" : ""}`;
    tab.dataset.id = slot.id;
    const label = document.createElement("span");
    label.textContent = slot.label;
    label.addEventListener("dblclick", () => {
      const input = document.createElement("input");
      input.type = "text"; input.value = slot.label; input.maxLength = 12;
      input.className = "new-patient-input";
      input.style.width = "80px"; input.style.display = "inline";
      tab.replaceChild(input, label);
      input.focus(); input.select();
      const finish = () => { renamePatientSlot(slot.id, input.value || slot.label); };
      input.addEventListener("blur", finish);
      input.addEventListener("keydown", e => { if (e.key === "Enter") input.blur(); if (e.key === "Escape") { input.value = slot.label; input.blur(); } });
    });
    tab.appendChild(label);
    tab.addEventListener("click", e => { if (e.target === label || e.target === tab) switchSlot(slot.id); });
    if (patientSlots.length > 1) {
      const close = document.createElement("button");
      close.className = "close-tab"; close.textContent = "×"; close.title = "移除";
      close.addEventListener("click", e => { e.stopPropagation(); removePatientSlot(slot.id); });
      tab.appendChild(close);
    }
    return tab;
  }));
}

function renderHistory() {
  if (!savedHistory.length) { els.historyList.innerHTML = '<div class="empty-state">尚未保存版本。</div>'; return; }
  els.historyList.replaceChildren(...savedHistory.map(item => {
    const node = document.createElement("div"); node.className = "history-item";
    const time = document.createElement("time"); time.textContent = new Date(item.createdAt).toLocaleString("zh-CN", { hour12: false });
    const preview = document.createElement("p"); preview.textContent = item.text.replace(/\s+/g, " ") || "（空白草稿）";
    const actions = document.createElement("div");
    const restore = document.createElement("button"); restore.textContent = "恢复"; restore.dataset.action = "restore"; restore.dataset.id = item.id;
    const remove = document.createElement("button"); remove.textContent = "删除"; remove.dataset.action = "delete"; remove.dataset.id = item.id;
    actions.append(restore, remove); node.append(time, preview, actions); return node;
  }));
}
async function persistHistory() {
  const slot = getActiveSlot();
  if (slot) slot.history = savedHistory;
  await persistPatientSlots();
  renderHistory();
}
async function saveSnapshot() {
  if (!els.draft.value.trim()) return setFeedback("草稿为空，无法保存版本。", true);
  savedHistory.unshift({ id: crypto.randomUUID(), createdAt: new Date().toISOString(), text: els.draft.value });
  savedHistory = savedHistory.slice(0, 20);
  const slot = getActiveSlot();
  if (slot) slot.history = savedHistory;
  await persistPatientSlots();
  renderHistory(); setFeedback("当前草稿已保存为本地版本。");
}

function exportText() {
  if (!els.draft.value.trim()) return setFeedback("草稿为空，无法导出。", true);
  const stamp = new Date().toISOString().slice(0, 19).replaceAll(":", "-");
  const blob = new Blob(["\ufeff", els.draft.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob); const link = document.createElement("a");
  link.href = url; link.download = `感染科病历草稿_${stamp}.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  setFeedback("TXT 文件已导出。请按医院规范妥善保存。");
}

function resampleTo16k(input, inputRate) {
  if (inputRate === 16000) return input; const ratio = inputRate / 16000;
  const output = new Float32Array(Math.round(input.length / ratio));
  for (let i = 0; i < output.length; i++) { const position = i * ratio, left = Math.floor(position), right = Math.min(left + 1, input.length - 1), fraction = position - left; output[i] = input[left] * (1 - fraction) + input[right] * fraction; }
  return output;
}
function encodeWav(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
  const write = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0,"RIFF"); view.setUint32(4,36+samples.length*2,true); write(8,"WAVE"); write(12,"fmt "); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true); view.setUint32(24,sampleRate,true); view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true); write(36,"data"); view.setUint32(40,samples.length*2,true);
  let offset=44; for (const sample of samples) { const clipped=Math.max(-1,Math.min(1,sample)); view.setInt16(offset,clipped<0?clipped*0x8000:clipped*0x7fff,true); offset+=2; }
  return new Blob([buffer],{type:"audio/wav"});
}
function updateTimer() { const total=Math.floor((Date.now()-startedAt)/1000); els.timer.textContent=`${String(Math.floor(total/60)).padStart(2,"0")}:${String(total%60).padStart(2,"0")}`; }

function drawWaveBar(rms) {
  const canvas = els.waveCanvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const imageData = ctx.getImageData(2, 0, w - 2, h);
  ctx.clearRect(0, 0, w, h);
  ctx.putImageData(imageData, 0, 0);
  const barHeight = Math.max(2, Math.min(h, rms * h * 12));
  ctx.fillStyle = rms < SILENCE_THRESHOLD ? "#d9e3df" : "#126a56";
  ctx.fillRect(w - 2, (h - barHeight) / 2, 2, barHeight);
}

function closeStreamingSocket() {
  if (ws) {
    ws.onopen = null; ws.onmessage = null; ws.onerror = null; ws.onclose = null;
    try { ws.close(); } catch {}
  }
  ws = null; wsConnected = false; streamingMode = false;
  streamReadyPromise = null; streamFinalPromise = null; chunkBuffer = [];
}

async function connectStreaming() {
  if (currentDictationLanguage() !== "zh-CN") throw new Error("English streaming is not enabled yet");
  const healthResp = await fetch(`${API_BASE}/health`, { cache: "no-store" });
  if (!healthResp.ok) throw new Error("本地服务不可用");
  const healthData = await healthResp.json();
  if (!healthData.streaming_supported) throw new Error("当前服务不支持流式识别");

  ws = new WebSocket(WS_URL);
  let resolveReady, rejectReady, resolveFinal, rejectFinal;
  streamReadyPromise = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  streamFinalPromise = new Promise((resolve, reject) => { resolveFinal = resolve; rejectFinal = reject; });
  let finalReceived = false;

  ws.onopen = () => {
    ws.send(JSON.stringify({ sample_rate: 16000, department: "infectious_disease", language: currentDictationLanguage(), chunk_size: [5, 10, 5] }));
  };
  ws.onmessage = event => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.type === "status" && msg.status === "loading") {
      setFeedback("正在准备流式识别模型；首次使用可能需要稍候……");
    } else if (msg.type === "ready") {
      wsConnected = true; resolveReady();
    } else if (msg.type === "partial") {
      els.partialDisplay.textContent = msg.text || "";
      els.partialDisplay.hidden = !msg.text;
    } else if (msg.type === "final") {
      finalReceived = true; resolveFinal(msg.text || "");
    } else if (msg.type === "error") {
      const error = new Error(msg.detail || "流式识别失败");
      rejectReady(error); rejectFinal(error);
    }
  };
  ws.onerror = () => {
    const error = new Error("无法连接流式识别服务");
    rejectReady(error); rejectFinal(error);
  };
  ws.onclose = () => {
    wsConnected = false;
    if (!finalReceived) rejectFinal(new Error("流式连接已关闭"));
  };

  await Promise.race([
    streamReadyPromise,
    new Promise((_, reject) => setTimeout(() => reject(new Error("流式模型准备超时")), 12000))
  ]);
  streamingMode = true;
}

function flushStreamingAudioBuffer() {
  if (!ws || ws.readyState !== WebSocket.OPEN || chunkBuffer.length === 0) return;
  const totalLen = chunkBuffer.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Int16Array(totalLen);
  let offset = 0;
  for (const chunk of chunkBuffer) { merged.set(chunk, offset); offset += chunk.length; }
  ws.send(merged.buffer);
  chunkBuffer = [];
}

function sendPauseMarker(durationMs, level) {
  if (!streamingMode || !wsConnected || ws?.readyState !== WebSocket.OPEN || pauseMarkLevel >= level) return;
  flushStreamingAudioBuffer();
  ws.send(JSON.stringify({ type: "pause", duration_ms: Math.round(durationMs) }));
  pauseMarkLevel = level;
  els.silenceIndicator.textContent = level === 2 ? "长停顿：句号" : "停顿：逗号";
  els.silenceIndicator.hidden = false;
}
async function startRecording() {
  if (recording || stoppingRecording) return;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
    audioContext = new AudioContext();
    if (audioContext.state === "suspended") await audioContext.resume();
    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);
    silentGain = audioContext.createGain(); silentGain.gain.value = 0;
    chunks = []; chunkBuffer = [];
    silenceFrames = 0; graceFrames = 0; voicedFrames = 0; pauseMarkLevel = 0;
    const frameSeconds = 4096 / audioContext.sampleRate;
    silenceMaxFrames = Math.ceil(3 / frameSeconds);
    graceMaxFrames = Math.ceil(1 / frameSeconds);
    commaPauseFrames = Math.ceil(0.65 / frameSeconds);
    periodPauseFrames = Math.ceil(1.4 / frameSeconds);

    closeStreamingSocket();
    if (currentDictationLanguage() === "zh-CN") {
      try {
        await connectStreaming();
      } catch (error) {
        closeStreamingSocket();
        setFeedback(`流式识别暂不可用，已切换为录音后识别：${error.message}`);
      }
    } else {
      setFeedback(uiLanguage === "en-US" ? "English dictation will transcribe after you stop recording." : "英文听写将在停止录音后进行识别。");
    }

    processorNode.onaudioprocess = event => {
      if (!recording) return;
      const buffer = new Float32Array(event.inputBuffer.getChannelData(0));
      chunks.push(buffer);
      let sumSq = 0;
      for (let i = 0; i < buffer.length; i++) sumSq += buffer[i] * buffer[i];
      const rms = Math.sqrt(sumSq / buffer.length);
      if (rms >= SILENCE_THRESHOLD) voicedFrames++;
      drawWaveBar(rms);

      if (streamingMode && wsConnected && ws?.readyState === WebSocket.OPEN) {
        const resampled = resampleTo16k(buffer, audioContext.sampleRate);
        const pcm16 = new Int16Array(resampled.length);
        for (let i = 0; i < resampled.length; i++) {
          const sample = Math.max(-1, Math.min(1, resampled[i]));
          pcm16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        }
        chunkBuffer.push(pcm16);
        const bufferedLength = chunkBuffer.reduce((sum, chunk) => sum + chunk.length, 0);
        if (bufferedLength >= 9600) {
          const merged = new Int16Array(bufferedLength);
          let offset = 0;
          for (const chunk of chunkBuffer) { merged.set(chunk, offset); offset += chunk.length; }
          ws.send(merged.buffer); chunkBuffer = [];
        }
      }

      graceFrames++;
      if (graceFrames > graceMaxFrames) {
        if (rms < SILENCE_THRESHOLD) {
          silenceFrames++;
          const pauseDurationMs = silenceFrames * (4096 / audioContext.sampleRate) * 1000;
          if (voicedFrames >= 3 && silenceFrames >= commaPauseFrames) sendPauseMarker(pauseDurationMs, 1);
          if (voicedFrames >= 3 && silenceFrames >= periodPauseFrames) sendPauseMarker(pauseDurationMs, 2);
          if (pauseMarkLevel === 0) els.silenceIndicator.hidden = silenceFrames < commaPauseFrames;
          if (silenceFrames >= silenceMaxFrames && !stoppingRecording) {
            setFeedback("已自动停止录音（检测到持续静音）。");
            stopRecording();
          }
        } else {
          silenceFrames = 0; pauseMarkLevel = 0; els.silenceIndicator.textContent = "静音检测中"; els.silenceIndicator.hidden = true;
        }
      }
    };
    sourceNode.connect(processorNode); processorNode.connect(silentGain); silentGain.connect(audioContext.destination);
    recording = true; startedAt = Date.now(); timerHandle = setInterval(updateTimer, 250);
    els.recordButton.classList.add("recording"); els.recordLabel.textContent = tr("stop_recording");
    document.querySelector(".editor-panel").classList.add("recording-active");
    setFeedback(streamingMode ? "正在流式识别，结果将实时显示……" : "正在本地录音；停止后发送给本机识别服务。");
  } catch (error) {
    mediaStream?.getTracks().forEach(track => track.stop());
    closeStreamingSocket();
    setFeedback(`无法使用麦克风：${error.message}`, true);
  }
}

async function stopRecording() {
  if (!recording || stoppingRecording) return;
  stoppingRecording = true; recording = false; clearInterval(timerHandle);
  processorNode?.disconnect(); sourceNode?.disconnect(); silentGain?.disconnect();
  mediaStream?.getTracks().forEach(track => track.stop());
  const sampleRate = audioContext?.sampleRate || 48000;
  if (audioContext && audioContext.state !== "closed") await audioContext.close();
  els.recordButton.classList.remove("recording"); els.recordLabel.textContent = tr("start_recording");
  document.querySelector(".editor-panel").classList.remove("recording-active");
  els.silenceIndicator.hidden = true;

  if (voicedFrames < 3) {
    els.partialDisplay.hidden = true; closeStreamingSocket(); stoppingRecording = false;
    els.recordButton.disabled = false; setFeedback("未检测到清晰语音，请靠近麦克风后重试。", true);
    return;
  }

  try {
    if (streamingMode && wsConnected && ws?.readyState === WebSocket.OPEN) {
      flushStreamingAudioBuffer();
      ws.send(JSON.stringify({ type: "end" }));
      els.recordButton.disabled = true; setFeedback("正在等待最终识别结果……");
      try {
        const finalText = await Promise.race([
          streamFinalPromise,
          new Promise((_, reject) => setTimeout(() => reject(new Error("等待最终结果超时")), 8000))
        ]);
        els.partialDisplay.hidden = true;
        if (finalText) mergeText(finalText);
        setFeedback("流式识别完成。请核对右侧提醒。");
        els.recordButton.disabled = false;
      } catch (error) {
        closeStreamingSocket();
        setFeedback(`流式识别未完成，已切换到批量识别：${error.message}`);
        await batchTranscribe(sampleRate);
      }
    } else {
      await batchTranscribe(sampleRate);
    }
  } finally {
    closeStreamingSocket(); stoppingRecording = false; els.recordButton.disabled = false;
  }
}
async function batchTranscribe(sampleRate) {
  els.recordButton.disabled = true;
  els.partialDisplay.hidden = true;
  setFeedback("正在本地识别，请稍候……");
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
  const form = new FormData();
  form.append("file", encodeWav(resampleTo16k(merged, sampleRate)), "dictation.wav");
  form.append("department", currentDictationLanguage() === "zh-CN" ? "infectious_disease" : "general");
  form.append("language", currentDictationLanguage());
  try {
    const response = await fetch(`${API_BASE}/transcribe`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "识别失败");
    mergeText(data.text);
    setFeedback(`识别完成，用时 ${data.elapsed_seconds.toFixed(1)} 秒。请核对右侧提醒。`);
  } catch (error) { setFeedback(`识别失败：${error.message}`, true); }
  finally { els.recordButton.disabled = false; checkService(); }
}


async function collectFeedbackDiagnostics() {
  const diagnostics = {
    extensionVersion: globalThis.chrome?.runtime?.getManifest ? chrome.runtime.getManifest().version : "local-preview",
    userAgent: navigator.userAgent,
    language: navigator.language,
    platform: navigator.platform,
    timestamp: new Date().toISOString(),
    service: null,
    license: null,
    draftLength: els.draft.value.length,
    patientCount: patientSlots.length,
    autosaveEnabled: els.autosaveToggle.checked
  };
  try {
    const response = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    diagnostics.service = await response.json();
  } catch (error) {
    diagnostics.service = { error: error.message };
  }
  try {
    const response = await fetch(`${API_BASE}/license/status`, { cache: "no-store" });
    diagnostics.license = await response.json();
  } catch (error) {
    diagnostics.license = { error: error.message };
  }
  return diagnostics;
}

async function openFeedbackModal() {
  const modal = document.getElementById("feedbackModal");
  const output = document.getElementById("feedbackDiagnostics");
  output.textContent = "正在收集版本和服务状态……";
  document.getElementById("feedbackMessage").value = "";
  modal.showModal();
  const diagnostics = await collectFeedbackDiagnostics();
  output.textContent = JSON.stringify(diagnostics, null, 2);
}

async function submitFeedback() {
  const message = document.getElementById("feedbackMessage").value.trim();
  if (message.length < 3) return setFeedback("请至少填写 3 个字的反馈内容。", true);
  const payload = {
    category: document.getElementById("feedbackCategory").value,
    rating: Number(document.getElementById("feedbackRating").value),
    message,
    contact: document.getElementById("feedbackContact").value.trim(),
    diagnostics: await collectFeedbackDiagnostics()
  };
  try {
    const response = await fetch(`${API_BASE}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "反馈保存失败");
    document.getElementById("feedbackModal").close();
    setFeedback(`反馈已保存到本机服务（编号 ${data.id.slice(0, 8)}）。谢谢，这种真实反馈最值钱。`);
  } catch (error) {
    setFeedback(`反馈保存失败：${error.message}`, true);
  }
}

async function copyFeedbackDiagnostics() {
  const diagnostics = await collectFeedbackDiagnostics();
  await navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2));
  setFeedback("诊断信息已复制，可粘贴给开发者排查问题。");
}

async function initialize() {
  const stored = await storageGet([STORAGE.autosave, STORAGE.uiLanguage, STORAGE.dictationLanguage]);
  els.autosaveToggle.checked = Boolean(stored[STORAGE.autosave]);
  els.uiLanguageSelect.value = stored[STORAGE.uiLanguage] || "zh-CN";
  els.dictationLanguageSelect.value = stored[STORAGE.dictationLanguage] || "zh-CN";
  applyUILanguage(els.uiLanguageSelect.value);
  updateLanguageSpecificUI();
  await loadPatientSlots();
  await loadTemplates();
  updateDraftMeta(); checkService(); loadHotwords();
}

els.uiLanguageSelect.addEventListener("change", async () => { applyUILanguage(els.uiLanguageSelect.value); await storageSet({ [STORAGE.uiLanguage]: els.uiLanguageSelect.value }); updateLanguageSpecificUI(); loadHotwordPacks(); });
els.dictationLanguageSelect.addEventListener("change", async () => { await storageSet({ [STORAGE.dictationLanguage]: els.dictationLanguageSelect.value }); updateLanguageSpecificUI(); });
els.recordButton.addEventListener("click",()=>recording?stopRecording():startRecording()); els.draft.addEventListener("input",updateDraftMeta);
els.insertTemplateButton.addEventListener("click",insertStructuredTemplate); els.saveHotwordsButton.addEventListener("click",saveHotwords); els.exportHotwordsButton.addEventListener("click", exportHotwordPacks); els.importHotwordsButton.addEventListener("click", () => els.importHotwordsInput.click()); els.importHotwordsInput.addEventListener("change", event => importHotwordFile(event.target.files?.[0])); els.snapshotButton.addEventListener("click",saveSnapshot); els.preSubmitCheckButton.addEventListener("click", () => renderPreSubmitChecklist(true)); els.exportButton.addEventListener("click",exportText); els.feedbackButton.addEventListener("click", openFeedbackModal);
document.getElementById("manageTemplatesButton").addEventListener("click", openTemplateManager);
document.getElementById("closeTemplateModal").addEventListener("click", () => document.getElementById("templateModal").close());
document.getElementById("closePreSubmitModal").addEventListener("click", () => document.getElementById("preSubmitModal").close());
document.getElementById("closePreSubmitButton").addEventListener("click", () => document.getElementById("preSubmitModal").close());
document.getElementById("copyAfterCheckButton").addEventListener("click", async () => { await copyDraftText(true); document.getElementById("preSubmitModal").close(); });
document.getElementById("closeFeedbackModal").addEventListener("click", () => document.getElementById("feedbackModal").close());
document.getElementById("cancelFeedbackButton").addEventListener("click", () => document.getElementById("feedbackModal").close());
document.getElementById("submitFeedbackButton").addEventListener("click", submitFeedback);
document.getElementById("copyDiagnosticsButton").addEventListener("click", copyFeedbackDiagnostics);
document.getElementById("addTemplateButton").addEventListener("click", () => openTemplateForm());
document.getElementById("saveTemplateBtn").addEventListener("click", saveTemplateForm);
document.getElementById("cancelTemplateBtn").addEventListener("click", () => { document.getElementById("templateManagerView").hidden = false; document.getElementById("templateFormView").hidden = true; });
document.getElementById("tplComposite").addEventListener("change", e => { document.getElementById("tplSections").hidden = !e.target.checked; });
els.copyButton.addEventListener("click", async () => copyDraftText(false));
els.clearButton.addEventListener("click",()=>{if(!els.draft.value)return;pushUndo();els.draft.value="";updateDraftMeta();setFeedback("草稿已清空，可点击撤销恢复。");});
els.undoButton.addEventListener("click",()=>{if(!undoHistory.length)return;els.draft.value=undoHistory.pop();els.undoButton.disabled=undoHistory.length===0;updateDraftMeta();setFeedback("已撤销上一步。");});
els.autosaveToggle.addEventListener("change",async()=>{await storageSet({[STORAGE.autosave]:els.autosaveToggle.checked});if(els.autosaveToggle.checked){saveCurrentSlotState();await persistPatientSlots();els.privacyText.textContent=tr("autosave_on");setFeedback("已开启本机草稿自动恢复。");}else{els.privacyText.textContent=tr("autosave_off");setFeedback("已关闭自动恢复。");}});
els.historyList.addEventListener("click",async event=>{const button=event.target.closest("button[data-id]");if(!button)return;const item=savedHistory.find(entry=>entry.id===button.dataset.id);if(button.dataset.action==="restore"&&item){pushUndo();els.draft.value=item.text;updateDraftMeta();setFeedback("已恢复所选版本。") }else if(button.dataset.action==="delete"){savedHistory=savedHistory.filter(entry=>entry.id!==button.dataset.id);await persistHistory();}});
els.clearHistoryButton.addEventListener("click",async()=>{savedHistory=[];await persistHistory();setFeedback("本地版本记录已清空。");});
document.getElementById("addPatientButton").addEventListener("click", () => {
  const input = document.getElementById("newPatientInput");
  input.hidden = false; input.value = ""; input.focus();
});
document.getElementById("newPatientInput").addEventListener("keydown", e => {
  if (e.key === "Enter") { const value = e.target.value; e.target.value = ""; e.target.hidden = true; addPatientSlot(value); }
  if (e.key === "Escape") { e.target.hidden = true; }
});
document.getElementById("newPatientInput").addEventListener("blur", e => { if (e.target.value.trim()) addPatientSlot(e.target.value); e.target.hidden = true; });
document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>{document.querySelectorAll(".tab").forEach(v=>v.classList.toggle("active",v===button));document.querySelectorAll(".tab-panel").forEach(panel=>panel.classList.toggle("active",panel.id===`${button.dataset.tab}Panel`));}));
document.addEventListener("keydown",event=>{if(event.code==="Space"&&document.activeElement!==els.draft&&document.activeElement!==els.hotwordEditor&&document.activeElement?.tagName!=="SELECT"&&document.activeElement?.tagName!=="BUTTON"){event.preventDefault();if(!els.recordButton.disabled)(recording?stopRecording():startRecording())}});

els.undoButton.disabled=true; initialize(); setInterval(checkService,15000);
