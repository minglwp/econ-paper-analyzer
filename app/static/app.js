"use strict";

const API = {
  upload: "/api/upload",
  demo: "/api/demo",
  analyze: "/api/analyze",
  job: (jobId) => `/api/jobs/${encodeURIComponent(jobId)}`,
  run: (runId) => `/api/runs/${encodeURIComponent(runId)}`,
  dataset: (datasetId, sheetName) => `/api/datasets/${encodeURIComponent(datasetId)}?${new URLSearchParams({ sheet_name: sheetName })}`,
};

const state = {
  file: null,
  dataset: null,
  scaleSequence: 0,
  modelSequence: 0,
  currentStep: "upload",
  jobId: null,
  runId: null,
  pollTimer: null,
  pollFailures: 0,
  seenLogs: new Set(),
};

const stepOrder = ["upload", "variables", "analysis", "results"];
const terminalStatuses = new Set(["completed", "completed_with_errors", "complete", "succeeded", "success", "done", "failed", "error", "cancelled"]);
const savedJobKey = "econ-paper-analyzer:last-job";
const maxPathModels = 20;

const els = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheElements();
  bindEvents();
  updateAnalysisCount();
  void resumeSavedJob();
});

function cacheElements() {
  [
    "datasetContext", "resetButton", "fileInput", "chooseFileButton", "uploadButton", "demoButton", "uploadZone",
    "fileLabel", "fileMeta", "uploadState", "uploadError", "dataReview", "dataCaption", "sheetField",
    "sheetSelect", "qualityStats", "columnCount", "columnQuality", "warningCount", "dataWarnings",
    "previewTable", "addScaleButton", "scaleList", "scaleEmpty", "scaleCount", "variableError", "variablesNext",
    "analysisToggles", "analysisCount", "addModelButton", "pathModelList", "pathModelEmpty", "modelCount",
    "correlationMethod", "harmanThreshold", "alphaInput",
    "bootstrapInput", "seedInput", "ciMethod", "robustSe", "analysisError", "runAnalysisButton",
    "jobBadge", "jobProgress", "progressTitle", "progressMessage", "progressPercent", "progressBar",
    "runLog", "jobError", "resultWorkspace", "resultSummary", "resultDiagnostics", "artifactList",
    "artifactCount", "backToSettings", "rerunButton", "scaleTemplate", "pathModelTemplate", "toastRegion",
  ].forEach((id) => { els[id] = document.getElementById(id); });
}

function bindEvents() {
  els.chooseFileButton.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", (event) => selectFile(event.target.files[0]));
  els.uploadButton.addEventListener("click", handleUpload);
  els.demoButton.addEventListener("click", handleDemo);
  els.resetButton.addEventListener("click", resetApplication);
  els.addScaleButton.addEventListener("click", () => addScale());
  els.addModelButton.addEventListener("click", () => addPathModel());
  els.variablesNext.addEventListener("click", handleVariablesNext);
  els.runAnalysisButton.addEventListener("click", handleRunAnalysis);
  els.rerunButton.addEventListener("click", handleRunAnalysis);
  els.backToSettings.addEventListener("click", () => showStep("analysis"));
  els.sheetSelect.addEventListener("change", handleSheetChange);

  document.querySelectorAll("[data-step-target]").forEach((button) => {
    button.addEventListener("click", () => showStep(button.dataset.stepTarget));
  });
  document.querySelectorAll("[data-next-step]").forEach((button) => {
    button.addEventListener("click", () => showStep(button.dataset.nextStep));
  });
  document.querySelectorAll("[data-prev-step]").forEach((button) => {
    button.addEventListener("click", () => showStep(button.dataset.prevStep));
  });
  document.querySelectorAll("[data-result-tab]").forEach((button) => {
    button.addEventListener("click", () => selectResultTab(button.dataset.resultTab));
    button.addEventListener("keydown", handleResultTabKeydown);
  });
  document.querySelectorAll("input[name='analysis']").forEach((input) => {
    input.addEventListener("change", updateAnalysisCount);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    els.uploadZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.uploadZone.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    els.uploadZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.uploadZone.classList.remove("is-dragging");
    });
  });
  els.uploadZone.addEventListener("drop", (event) => selectFile(event.dataTransfer.files[0]));
}

// API adapter functions keep backend response mapping in one place.
async function uploadDataset(file) {
  const response = await fetch(API.upload, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  return parseApiResponse(response);
}

async function startAnalysis(payload) {
  const response = await fetch(API.analyze, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseApiResponse(response);
}

async function loadDemo() {
  const response = await fetch(API.demo, { method: "POST", headers: { "Accept": "application/json" } });
  return parseApiResponse(response);
}

async function getDatasetSheet(datasetId, sheetName) {
  const response = await fetch(API.dataset(datasetId, sheetName), { headers: { "Accept": "application/json" } });
  return parseApiResponse(response);
}

async function getJob(jobId) {
  const response = await fetch(API.job(jobId), { headers: { "Accept": "application/json" } });
  return parseApiResponse(response);
}

async function getRun(runId) {
  const response = await fetch(API.run(runId), { headers: { "Accept": "application/json" } });
  return parseApiResponse(response);
}

async function parseApiResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : { message: await response.text() };
  if (!response.ok) {
    const detail = data.detail || data.error || data.message || `请求失败（HTTP ${response.status}）`;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || JSON.stringify(item)).join("；")
      : String(detail);
    throw new Error(message);
  }
  return data;
}

function normalizeUploadResponse(raw, file) {
  const summary = raw.summary || raw.quality || raw.data_quality || {};
  const preview = Array.isArray(raw.preview)
    ? raw.preview
    : Array.isArray(raw.rows)
      ? raw.rows
      : Array.isArray(raw.sample)
        ? raw.sample
        : [];
  const rawColumns = raw.columns || summary.columns_detail || summary.fields || Object.keys(preview[0] || {});
  const columns = rawColumns.map((column) => {
    if (typeof column === "string") {
      const numericShare = firstNumber(summary.numeric_share?.[column]);
      const missing = firstNumber(summary.missing_by_column?.[column]) ?? 0;
      return {
        name: column,
        dtype: numericShare === null ? "未知" : numericShare >= 0.5 ? "数值" : "文本",
        missing,
        missingRate: null,
        numeric: numericShare === null || numericShare >= 0.5,
      };
    }
    const name = String(column.name ?? column.column ?? column.field ?? "");
    const missing = firstNumber(column.missing, column.missing_count, column.null_count);
    let missingRate = firstNumber(column.missing_rate, column.null_rate, column.missing_ratio);
    if (missingRate !== null && missingRate <= 1) missingRate *= 100;
    const dtype = String(column.dtype ?? column.type ?? column.data_type ?? "未知");
    const numeric = column.numeric ?? column.is_numeric ?? /int|float|double|number|decimal/i.test(dtype);
    return { name, dtype, missing, missingRate, numeric: Boolean(numeric) };
  }).filter((column) => column.name);

  const rows = firstNumber(raw.rows, raw.row_count, raw.rows_count, summary.rows, summary.row_count, raw.n_rows) ?? preview.length;
  const columnTotal = firstNumber(raw.columns_count, raw.column_count, summary.columns, summary.column_count, raw.n_columns) ?? columns.length;
  const missingCells = firstNumber(raw.missing_cells, summary.missing_cells, summary.missing, summary.null_cells)
    ?? columns.reduce((sum, column) => sum + (column.missing || 0), 0);
  const duplicateRows = firstNumber(raw.duplicate_rows, summary.duplicate_rows, summary.duplicates) ?? 0;
  const numericColumns = firstNumber(raw.numeric_columns, summary.numeric_columns)
    ?? columns.filter((column) => column.numeric).length;
  const sheets = raw.sheets || raw.sheet_names || summary.sheets || [];
  const warnings = normalizeMessages(raw.warnings || summary.warnings || raw.diagnostics || []);
  const emptyColumns = Array.isArray(summary.empty_columns) ? summary.empty_columns : [];
  const constantColumns = Array.isArray(summary.constant_columns) ? summary.constant_columns : [];
  if (emptyColumns.length) warnings.push(`空字段：${emptyColumns.join("、")}`);
  if (constantColumns.length) warnings.push(`常量字段：${constantColumns.join("、")}`);

  return {
    id: String(raw.dataset_id || raw.id || raw.dataset?.id || ""),
    filename: raw.filename || raw.file_name || file.name,
    sheetName: raw.selected_sheet || raw.sheet_name || raw.active_sheet || sheets[0] || null,
    sheets: Array.isArray(sheets) ? sheets.map(String) : [],
    rows,
    columnTotal,
    missingCells,
    duplicateRows,
    numericColumns,
    columns,
    preview,
    warnings,
  };
}

function normalizeJobResponse(raw, fallbackId) {
  const job = raw.job || raw;
  const rawProgress = firstNumber(job.progress, job.percent, job.progress_percent);
  const progress = rawProgress === null ? null : Math.max(0, Math.min(100, rawProgress <= 1 ? rawProgress * 100 : rawProgress));
  const result = job.result || job.results || job.output || null;
  const status = String(job.status || job.state || (result ? "completed" : "queued")).toLowerCase();
  const artifacts = job.artifacts || result?.artifacts || job.downloads || [];
  const diagnostics = job.diagnostics || result?.diagnostics || result?.model_diagnostics || [];
  let summary = result?.summary || job.summary || result || {};
  if (Array.isArray(result?.path_models) && isPlainObject(summary)) {
    summary = { ...summary, path_models: result.path_models };
  }
  return {
    id: String(job.job_id || job.id || fallbackId || ""),
    runId: String(job.run_id || job.runId || ""),
    status,
    progress: progress ?? inferredProgress(status),
    title: job.stage_label || job.stage || statusLabel(status),
    message: job.message || job.detail || job.current_step || "",
    error: job.error || (status === "failed" ? job.detail : "") || "",
    logs: normalizeMessages(job.logs || job.events || job.history || []),
    summary,
    diagnostics,
    artifacts: normalizeArtifacts(artifacts),
  };
}

function normalizeArtifacts(rawArtifacts) {
  const entries = Array.isArray(rawArtifacts)
    ? rawArtifacts
    : Object.entries(rawArtifacts || {}).map(([name, value]) => typeof value === "string" ? { name, url: value } : { name, ...value });
  return entries.map((artifact, index) => {
    if (typeof artifact === "string") {
      const filename = artifact.split("/").pop() || `文件 ${index + 1}`;
      return { name: filename, filename, url: artifact, type: fileExtension(artifact) };
    }
    const url = artifact.url || artifact.href || artifact.download_url || artifact.path || "";
    const filename = artifact.filename || artifact.name || String(url).split("/").pop() || `文件 ${index + 1}`;
    const name = artifact.label || artifact.title || filename;
    return { name: String(name), filename: String(filename), url: String(url), type: String(artifact.type || fileExtension(url || filename) || "FILE") };
  }).filter((artifact) => artifact.url);
}

function selectFile(file) {
  hideAlert(els.uploadError);
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (!new Set(["csv", "xlsx"]).has(extension)) {
    showAlert(els.uploadError, "请选择 CSV 或 XLSX 文件。");
    return;
  }
  if (file.size > 100 * 1024 * 1024) {
    showAlert(els.uploadError, "文件超过 100 MB 限制。");
    return;
  }
  state.file = file;
  els.fileLabel.textContent = file.name;
  els.fileMeta.textContent = `${fileExtension(file.name)} · ${formatBytes(file.size)}`;
  els.uploadButton.disabled = false;
  els.uploadState.textContent = "待上传";
}

async function handleUpload() {
  if (!state.file) return;
  setUploading(true);
  hideAlert(els.uploadError);
  try {
    const raw = await uploadDataset(state.file);
    state.dataset = normalizeUploadResponse(raw, state.file);
    if (!state.dataset.id) throw new Error("上传响应缺少 dataset_id。");
    clearVariableConfiguration();
    lockStepsAfter("variables");
    renderDataset(state.dataset);
    unlockStep("variables");
    els.uploadState.textContent = "已就绪";
    showToast("数据已导入");
  } catch (error) {
    showAlert(els.uploadError, error.message);
    els.uploadState.textContent = "上传失败";
  } finally {
    setUploading(false);
  }
}

async function handleDemo() {
  setUploading(true);
  hideAlert(els.uploadError);
  els.demoButton.disabled = true;
  try {
    const raw = await loadDemo();
    state.file = null;
    state.dataset = normalizeUploadResponse(raw, { name: raw.filename || "demo_survey.csv" });
    clearVariableConfiguration();
    lockStepsAfter("variables");
    renderDataset(state.dataset);
    unlockStep("variables");
    (raw.suggested_config?.scales || []).forEach((scale) => addScale(scale));
    updateModelOptions();
    suggestedPathModels(raw.suggested_config || {}).forEach((model) => addPathModel(model, { focus: false }));
    els.datasetContext.textContent = `示例数据 · ${formatInteger(state.dataset.rows)} 行 × ${formatInteger(state.dataset.columnTotal)} 列`;
    els.uploadState.textContent = "示例已就绪";
    showToast("示例数据与模型配置已载入");
  } catch (error) {
    showAlert(els.uploadError, error.message);
    els.uploadState.textContent = "载入失败";
  } finally {
    setUploading(false);
    els.demoButton.disabled = false;
  }
}

function setUploading(uploading) {
  els.uploadZone.classList.toggle("is-uploading", uploading);
  els.chooseFileButton.disabled = uploading;
  els.demoButton.disabled = uploading;
  els.uploadButton.disabled = uploading || !state.file;
  els.uploadButton.textContent = uploading ? "上传中…" : "上传";
  if (uploading) els.uploadState.textContent = "读取中";
}

function renderDataset(dataset) {
  els.datasetContext.textContent = `${dataset.filename} · ${formatInteger(dataset.rows)} 行 × ${formatInteger(dataset.columnTotal)} 列`;
  els.dataCaption.textContent = dataset.sheetName ? `${dataset.filename} · ${dataset.sheetName}` : dataset.filename;
  els.dataReview.classList.remove("is-hidden");
  renderSheets(dataset);
  renderQualityStats(dataset);
  renderColumnQuality(dataset.columns, dataset.rows);
  renderWarnings(dataset.warnings, dataset);
  renderPreview(dataset.preview, dataset.columns);
  populateVariableControls();
}

function renderSheets(dataset) {
  els.sheetSelect.replaceChildren();
  if (dataset.sheets.length < 2) {
    els.sheetField.classList.add("is-hidden");
    return;
  }
  dataset.sheets.forEach((sheet) => els.sheetSelect.add(new Option(sheet, sheet, false, sheet === dataset.sheetName)));
  els.sheetField.classList.remove("is-hidden");
}

async function handleSheetChange() {
  if (!state.dataset || !els.sheetSelect.value || els.sheetSelect.value === state.dataset.sheetName) return;
  const previousSheet = state.dataset.sheetName;
  const selectedSheet = els.sheetSelect.value;
  hideAlert(els.uploadError);
  els.sheetSelect.disabled = true;
  els.uploadState.textContent = "读取中";
  try {
    const raw = await getDatasetSheet(state.dataset.id, selectedSheet);
    const filename = state.dataset.filename;
    state.dataset = normalizeUploadResponse(raw, { name: filename });
    clearVariableConfiguration();
    lockStepsAfter("variables");
    renderDataset(state.dataset);
    els.uploadState.textContent = "已就绪";
    showToast(`已切换至 ${selectedSheet}`);
  } catch (error) {
    els.sheetSelect.value = previousSheet || "";
    showAlert(els.uploadError, error.message);
    els.uploadState.textContent = "读取失败";
  } finally {
    els.sheetSelect.disabled = false;
  }
}

function renderQualityStats(dataset) {
  const totalCells = dataset.rows * dataset.columnTotal;
  const missingRate = totalCells ? (dataset.missingCells / totalCells) * 100 : 0;
  const stats = [
    ["样本量", formatInteger(dataset.rows)],
    ["字段数", formatInteger(dataset.columnTotal)],
    ["数值字段", formatInteger(dataset.numericColumns)],
    ["缺失单元格", `${formatInteger(dataset.missingCells)} (${formatPercent(missingRate)})`],
    ["重复行", formatInteger(dataset.duplicateRows)],
  ];
  els.qualityStats.replaceChildren(...stats.map(([label, value]) => {
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    wrapper.append(term, description);
    return wrapper;
  }));
}

function renderColumnQuality(columns, rowCount) {
  els.columnCount.textContent = `${columns.length} 个字段`;
  els.columnQuality.replaceChildren(...columns.map((column) => {
    const missingRate = column.missingRate ?? (rowCount && column.missing !== null ? column.missing / rowCount * 100 : 0);
    const row = document.createElement("div");
    row.className = "column-row";
    const name = document.createElement("span");
    name.className = "column-name";
    name.title = column.name;
    name.textContent = column.name;
    const type = document.createElement("span");
    type.className = "type-tag";
    type.textContent = column.dtype;
    const meter = document.createElement("span");
    meter.className = "missing-meter";
    const track = document.createElement("span");
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(0, Math.min(100, missingRate))}%`;
    track.append(fill);
    const label = document.createElement("span");
    label.textContent = `${formatPercent(missingRate)} 缺失`;
    meter.append(track, label);
    row.append(name, type, meter);
    return row;
  }));
}

function renderWarnings(warnings, dataset) {
  const messages = [...warnings];
  if (!messages.length && dataset.missingCells === 0 && dataset.duplicateRows === 0) {
    messages.push("未发现缺失值或重复行");
  } else {
    if (dataset.missingCells > 0 && !messages.some((message) => message.includes("缺失"))) messages.push(`检测到 ${dataset.missingCells} 个缺失单元格`);
    if (dataset.duplicateRows > 0 && !messages.some((message) => message.includes("重复"))) messages.push(`检测到 ${dataset.duplicateRows} 行重复数据`);
  }
  els.warningCount.textContent = `${messages.length} 项`;
  els.dataWarnings.replaceChildren(...messages.map((message) => {
    const item = document.createElement("li");
    item.textContent = message;
    if (messages.length === 1 && message.includes("未发现")) item.className = "is-ok";
    return item;
  }));
}

function renderPreview(rows, columns) {
  const tableColumns = columns.length ? columns.map((column) => column.name) : Object.keys(rows[0] || {});
  const headRow = document.createElement("tr");
  tableColumns.forEach((column) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = column;
    th.title = column;
    headRow.append(th);
  });
  els.previewTable.tHead.replaceChildren(headRow);
  const bodyRows = rows.slice(0, 8).map((row) => {
    const tr = document.createElement("tr");
    tableColumns.forEach((column, index) => {
      const td = document.createElement("td");
      const value = Array.isArray(row) ? row[index] : row[column];
      td.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
      td.title = td.textContent;
      tr.append(td);
    });
    return tr;
  });
  els.previewTable.tBodies[0].replaceChildren(...bodyRows);
}

function addScale(initial = {}) {
  if (!state.dataset) return;
  state.scaleSequence += 1;
  const editor = els.scaleTemplate.content.firstElementChild.cloneNode(true);
  editor.dataset.scaleId = String(state.scaleSequence);
  editor.querySelector(".scale-index span").textContent = state.scaleSequence;
  editor.querySelector(".scale-name").value = initial.name || "";
  editor.querySelector(".scale-min").value = initial.minimum ?? 1;
  editor.querySelector(".scale-max").value = initial.maximum ?? 5;
  const alreadyAssigned = new Set(assignedScaleItems());
  (initial.items || [])
    .filter((item) => !alreadyAssigned.has(item) && questionColumnNames().includes(item))
    .forEach((item) => appendSelectedScaleItem(editor, item, (initial.reverse_items || []).includes(item)));
  editor.querySelector(".assign-items").addEventListener("click", () => assignScaleItems(editor));
  editor.querySelector(".unassign-items").addEventListener("click", () => unassignScaleItems(editor));
  editor.querySelector(".scale-available-items").addEventListener("dblclick", () => assignScaleItems(editor));
  editor.querySelector(".scale-selected-list").addEventListener("keydown", (event) => handleSelectedItemKeydown(event, editor));
  editor.querySelector(".scale-name").addEventListener("input", () => {
    updateModelOptions();
    validateScaleEditor(editor, false);
  });
  editor.querySelector(".scale-min").addEventListener("input", () => validateScaleEditor(editor, false));
  editor.querySelector(".scale-max").addEventListener("input", () => validateScaleEditor(editor, false));
  editor.querySelector(".remove-scale").addEventListener("click", () => {
    editor.remove();
    refreshScaleIndices();
    refreshScaleItemPickers();
    updateModelOptions();
  });
  els.scaleList.append(editor);
  refreshScaleIndices();
  refreshScaleItemPickers();
  updateModelOptions();
  editor.querySelector(".scale-name").focus();
}

function questionColumnNames() {
  return (state.dataset?.columns || []).filter((column) => column.numeric).map((column) => column.name);
}

function assignedScaleItems() {
  return [...els.scaleList.querySelectorAll(".picker-selected-item")].map((row) => row.dataset.item);
}

function scaleItems(editor) {
  return [...editor.querySelectorAll(".picker-selected-item")].map((row) => row.dataset.item);
}

function reverseScaleItems(editor) {
  return [...editor.querySelectorAll(".picker-selected-item")]
    .filter((row) => row.querySelector(".reverse-checkbox").checked)
    .map((row) => row.dataset.item);
}

function appendSelectedScaleItem(editor, item, reverse = false) {
  if (!item || scaleItems(editor).includes(item)) return;
  const list = editor.querySelector(".scale-selected-list");
  list.querySelector(".picker-empty")?.remove();
  const row = document.createElement("div");
  row.className = "picker-selected-item";
  row.dataset.item = item;
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", "false");
  row.tabIndex = 0;

  const name = document.createElement("span");
  name.className = "picker-item-name";
  name.textContent = item;
  name.title = item;
  const reverseLabel = document.createElement("label");
  reverseLabel.className = "reverse-choice";
  reverseLabel.title = `将 ${item} 标记为反向题`;
  const reverseInput = document.createElement("input");
  reverseInput.type = "checkbox";
  reverseInput.className = "reverse-checkbox";
  reverseInput.checked = reverse;
  reverseInput.setAttribute("aria-label", `${item} 是反向题`);
  const reverseText = document.createElement("span");
  reverseText.textContent = "反向";
  reverseLabel.append(reverseInput, reverseText);
  row.append(name, reverseLabel);
  row.addEventListener("click", (event) => {
    if (event.target.closest(".reverse-choice")) return;
    toggleSelectedScaleRow(row);
  });
  row.addEventListener("dblclick", (event) => {
    if (event.target.closest(".reverse-choice")) return;
    row.remove();
    refreshScaleItemPickers();
    validateScaleEditor(editor, false);
  });
  list.append(row);
}

function toggleSelectedScaleRow(row) {
  const selected = row.getAttribute("aria-selected") !== "true";
  row.setAttribute("aria-selected", String(selected));
  row.classList.toggle("is-selected", selected);
}

function assignScaleItems(editor) {
  const available = editor.querySelector(".scale-available-items");
  const items = selectedOptions(available);
  if (!items.length) return;
  items.forEach((item) => appendSelectedScaleItem(editor, item));
  refreshScaleItemPickers();
  validateScaleEditor(editor, false);
}

function unassignScaleItems(editor) {
  const rows = [...editor.querySelectorAll('.picker-selected-item[aria-selected="true"]')];
  if (!rows.length) return;
  rows.forEach((row) => row.remove());
  refreshScaleItemPickers();
  validateScaleEditor(editor, false);
}

function handleSelectedItemKeydown(event, editor) {
  const row = event.target.closest(".picker-selected-item");
  if (!row) return;
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    toggleSelectedScaleRow(row);
  }
  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    row.remove();
    refreshScaleItemPickers();
    validateScaleEditor(editor, false);
  }
}

function refreshScaleItemPickers() {
  const assigned = new Set(assignedScaleItems());
  [...els.scaleList.querySelectorAll(".scale-editor")].forEach((editor) => {
    const available = editor.querySelector(".scale-available-items");
    const previousSelection = selectedOptions(available);
    const availableItems = questionColumnNames().filter((item) => !assigned.has(item));
    available.replaceChildren(...availableItems.map((item) => new Option(item, item, false, previousSelection.includes(item))));
    editor.querySelector(".available-count").textContent = `${availableItems.length} 项`;
    const selectedItems = scaleItems(editor);
    editor.querySelector(".selected-count").textContent = `${selectedItems.length} 项`;
    const selectedList = editor.querySelector(".scale-selected-list");
    if (!selectedItems.length && !selectedList.querySelector(".picker-empty")) {
      const empty = document.createElement("div");
      empty.className = "picker-empty";
      empty.textContent = "尚未选择题项";
      selectedList.append(empty);
    }
    editor.querySelector(".assign-items").disabled = availableItems.length === 0;
    editor.querySelector(".unassign-items").disabled = selectedItems.length === 0;
  });
}

function refreshScaleIndices() {
  const editors = [...els.scaleList.querySelectorAll(".scale-editor")];
  editors.forEach((editor, index) => {
    editor.querySelector(".scale-index span").textContent = index + 1;
    editor.querySelector(".remove-scale").setAttribute("aria-label", `删除量表 ${index + 1}`);
  });
  els.scaleEmpty.classList.toggle("is-hidden", editors.length > 0);
  els.scaleCount.textContent = `${editors.length} 个量表`;
}

function populateVariableControls() {
  refreshScaleItemPickers();
  updateModelOptions();
}

function modelVariableChoices() {
  const scaleNames = [...els.scaleList.querySelectorAll(".scale-name")]
    .map((input) => input.value.trim())
    .filter(Boolean);
  const baseNames = questionColumnNames();
  return { scaleNames: [...new Set(scaleNames)], baseNames };
}

function updateModelOptions() {
  if (!state.dataset) return;
  [...els.pathModelList.querySelectorAll(".path-model-editor")].forEach((editor) => populateModelEditorOptions(editor));
}

function populateModelEditorOptions(editor, initial = null) {
  const { scaleNames, baseNames } = modelVariableChoices();
  const choices = [...new Set([...scaleNames, ...baseNames])];
  const values = initial || {
    x: editor.querySelector(".model-x").value,
    y: editor.querySelector(".model-y").value,
    mediator: editor.querySelector(".model-mediator").value,
    moderator: editor.querySelector(".model-moderator").value,
    controls: selectedOptions(editor.querySelector(".model-controls")),
  };
  const mappings = [
    [editor.querySelector(".model-x"), values.x],
    [editor.querySelector(".model-y"), values.y],
    [editor.querySelector(".model-mediator"), values.mediator],
    [editor.querySelector(".model-moderator"), values.moderator],
  ];
  mappings.forEach(([select, previous]) => {
    select.replaceChildren(new Option("请选择", ""));
    appendGroupedOptions(select, scaleNames, baseNames);
    if (choices.includes(previous)) select.value = previous;
  });
  const controls = editor.querySelector(".model-controls");
  controls.replaceChildren();
  appendGroupedOptions(controls, scaleNames, baseNames);
  [...controls.options].forEach((option) => {
    option.selected = (values.controls || []).includes(option.value);
  });
}

function appendGroupedOptions(select, scaleNames, baseNames) {
  if (scaleNames.length) {
    const scaleGroup = document.createElement("optgroup");
    scaleGroup.label = "构念";
    [...new Set(scaleNames)].forEach((name) => scaleGroup.append(new Option(name, name)));
    select.append(scaleGroup);
  }
  const columnGroup = document.createElement("optgroup");
  columnGroup.label = "原始字段";
  baseNames.filter((name) => !scaleNames.includes(name)).forEach((name) => columnGroup.append(new Option(name, name)));
  select.append(columnGroup);
}

function validateScaleEditor(editor, announce = true) {
  const name = editor.querySelector(".scale-name").value.trim();
  const items = scaleItems(editor);
  const minimum = Number(editor.querySelector(".scale-min").value);
  const maximum = Number(editor.querySelector(".scale-max").value);
  let message = "";
  if (!name) message = "请输入构念名称。";
  else if (items.length < 2) message = "每个量表至少选择 2 个题项。";
  else if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum >= maximum) message = "量表最小值必须小于最大值。";
  editor.querySelector(".scale-validation").textContent = announce ? message : "";
  return !message;
}

function handleVariablesNext() {
  hideAlert(els.variableError);
  const editors = [...els.scaleList.querySelectorAll(".scale-editor")];
  const scalesValid = editors.every((editor) => validateScaleEditor(editor));
  const scaleNames = editors.map((editor) => editor.querySelector(".scale-name").value.trim()).filter(Boolean);
  if (!scalesValid) {
    showAlert(els.variableError, "请完成量表配置后继续。");
    return;
  }
  if (new Set(scaleNames).size !== scaleNames.length) {
    showAlert(els.variableError, "量表名称不能重复。");
    return;
  }
  updateModelOptions();
  unlockStep("analysis");
  showStep("analysis");
}

function collectScales() {
  return [...els.scaleList.querySelectorAll(".scale-editor")].map((editor) => ({
    name: editor.querySelector(".scale-name").value.trim(),
    items: scaleItems(editor),
    reverse_items: reverseScaleItems(editor),
    minimum: Number(editor.querySelector(".scale-min").value),
    maximum: Number(editor.querySelector(".scale-max").value),
  }));
}

function suggestedPathModels(config) {
  if (Array.isArray(config.models) && config.models.length) return config.models;
  const roles = config.roles || {};
  if (!roles.x || !roles.y) return [];
  return [{
    name: "被调节的中介模型（Model 7）",
    analysis: roles.mediator && roles.moderator ? "moderated_mediation" : roles.mediator ? "mediation" : roles.moderator ? "moderation" : "regression",
    x: roles.x,
    y: roles.y,
    mediator: roles.mediator || null,
    moderator: roles.moderator || null,
    controls: roles.controls || [],
    moderated_stage: "first",
  }];
}

function addPathModel(initial = {}, options = {}) {
  if (!state.dataset) return;
  const existingCount = els.pathModelList.querySelectorAll(".path-model-editor").length;
  if (existingCount >= maxPathModels) {
    showToast(`最多可添加 ${maxPathModels} 条路径。`, true);
    return;
  }
  state.modelSequence += 1;
  const editor = els.pathModelTemplate.content.firstElementChild.cloneNode(true);
  editor.dataset.modelId = String(state.modelSequence);
  editor.querySelector(".model-name").value = initial.name || `路径模型 ${state.modelSequence}`;
  editor.querySelector(".model-analysis").value = initial.analysis || initial.type || "regression";
  const stageName = `model-stage-${state.modelSequence}`;
  editor.querySelectorAll(".model-stage-field input").forEach((input) => { input.name = stageName; });
  const stage = initial.moderated_stage || initial.moderatedStage || "first";
  editor.querySelector(`.model-stage-${stage === "second" ? "second" : "first"}`).checked = true;
  populateModelEditorOptions(editor, {
    x: initial.x || "",
    y: initial.y || "",
    mediator: initial.mediator || initial.m || "",
    moderator: initial.moderator || initial.w || "",
    controls: initial.controls || [],
  });
  editor.querySelector(".model-analysis").addEventListener("change", () => {
    updateModelVisibility(editor);
    validatePathModelEditor(editor, false);
    updateAnalysisCount();
  });
  editor.querySelectorAll("input, select").forEach((input) => {
    input.addEventListener("change", () => validatePathModelEditor(editor, false));
  });
  editor.querySelector(".model-name").addEventListener("input", () => validatePathModelEditor(editor, false));
  editor.querySelector(".remove-model").addEventListener("click", () => {
    editor.remove();
    refreshPathModelIndices();
  });
  editor.querySelector(".duplicate-model").addEventListener("click", () => {
    const copy = collectPathModel(editor);
    copy.name = duplicatePathModelName(copy.name);
    addPathModel(copy);
  });
  els.pathModelList.append(editor);
  updateModelVisibility(editor);
  refreshPathModelIndices();
  if (options.focus !== false) editor.querySelector(".model-name").focus();
}

function duplicatePathModelName(name) {
  const existing = new Set(collectPathModels().map((model) => model.name));
  for (let number = 1; number <= maxPathModels; number += 1) {
    const suffix = number === 1 ? "（副本）" : `（副本 ${number}）`;
    const candidate = `${name.slice(0, 64 - suffix.length)}${suffix}`;
    if (!existing.has(candidate)) return candidate;
  }
  return `路径模型 ${state.modelSequence + 1}`;
}

function updateModelVisibility(editor) {
  const analysis = editor.querySelector(".model-analysis").value;
  const needsMediator = analysis === "mediation" || analysis === "moderated_mediation";
  const needsModerator = analysis === "moderation" || analysis === "moderated_mediation";
  editor.querySelector(".model-mediator-field").classList.toggle("is-hidden", !needsMediator);
  editor.querySelector(".model-moderator-field").classList.toggle("is-hidden", !needsModerator);
  editor.querySelector(".model-stage-field").classList.toggle("is-hidden", analysis !== "moderated_mediation");
}

function refreshPathModelIndices() {
  const editors = [...els.pathModelList.querySelectorAll(".path-model-editor")];
  editors.forEach((editor, index) => {
    editor.querySelector(".model-index span").textContent = index + 1;
    editor.querySelector(".duplicate-model").setAttribute("aria-label", `复制路径 ${index + 1}`);
    editor.querySelector(".remove-model").setAttribute("aria-label", `删除路径 ${index + 1}`);
  });
  els.pathModelEmpty.classList.toggle("is-hidden", editors.length > 0);
  els.modelCount.textContent = `${editors.length} 个模型`;
  els.addModelButton.disabled = editors.length >= maxPathModels;
  updateAnalysisCount();
}

function collectPathModel(editor) {
  const analysis = editor.querySelector(".model-analysis").value;
  const stage = editor.querySelector(".model-stage-field input:checked")?.value || "first";
  return {
    name: editor.querySelector(".model-name").value.trim(),
    analysis,
    x: editor.querySelector(".model-x").value,
    y: editor.querySelector(".model-y").value,
    mediator: analysis === "mediation" || analysis === "moderated_mediation" ? editor.querySelector(".model-mediator").value || null : null,
    moderator: analysis === "moderation" || analysis === "moderated_mediation" ? editor.querySelector(".model-moderator").value || null : null,
    controls: selectedOptions(editor.querySelector(".model-controls")),
    moderated_stage: analysis === "moderated_mediation" ? stage : "first",
  };
}

function collectPathModels() {
  return [...els.pathModelList.querySelectorAll(".path-model-editor")].map(collectPathModel);
}

function validatePathModelEditor(editor, announce = true) {
  const model = collectPathModel(editor);
  const roles = [model.x, model.y, model.mediator, model.moderator].filter(Boolean);
  let message = "";
  if (!model.name) message = "请输入模型名称。";
  else if (!model.x || !model.y) message = "请选择自变量 X 和因变量 Y。";
  else if ((model.analysis === "mediation" || model.analysis === "moderated_mediation") && !model.mediator) message = "该模型需要选择中介变量 M。";
  else if ((model.analysis === "moderation" || model.analysis === "moderated_mediation") && !model.moderator) message = "该模型需要选择调节变量 W。";
  else if (new Set(roles).size !== roles.length) message = "X、Y、M、W 需要使用不同变量。";
  else if (model.controls.some((control) => roles.includes(control))) message = "控制变量不能与 X、Y、M、W 重复。";
  editor.querySelector(".model-validation").textContent = announce ? message : "";
  return !message;
}

function collectAnalysisPayload() {
  const analysisInputs = [...document.querySelectorAll("input[name='analysis']")];
  const analyses = Object.fromEntries(analysisInputs.map((input) => [input.value, input.checked]));
  analyses.regression = false;
  analyses.mediation = false;
  analyses.moderation = false;
  analyses.moderated_mediation = false;
  analyses.correlation = els.correlationMethod.value;
  return {
    dataset_id: state.dataset.id,
    sheet_name: els.sheetSelect.value || state.dataset.sheetName || null,
    missing_codes: ["", 999],
    scales: collectScales(),
    models: collectPathModels(),
    analyses,
    inference: {
      alpha: Number(els.alphaInput.value),
      bootstrap_samples: Number(els.bootstrapInput.value),
      seed: Number(els.seedInput.value),
      confidence_interval: els.ciMethod.value,
      robust_se: els.robustSe.value,
      harman_threshold: Number(els.harmanThreshold.value),
    },
  };
}

function validateAnalysis(payload) {
  const selected = Object.entries(payload.analyses).filter(([key, value]) => typeof value === "boolean" && value).map(([key]) => key);
  if (!selected.length && !payload.models.length) return "请至少选择一项全局分析或添加一条回归路径。";
  if (["cfa", "harman", "ulmc"].some((name) => payload.analyses[name]) && !payload.scales.length) return "CFA 与共同方法偏差检验需要至少一个量表。";
  if (payload.analyses.descriptives && !payload.scales.length && !payload.models.length) return "描述性统计至少需要一个量表或一条回归路径。";
  const modelEditors = [...els.pathModelList.querySelectorAll(".path-model-editor")];
  const modelsValid = modelEditors.map((editor) => validatePathModelEditor(editor)).every(Boolean);
  if (!modelsValid) return "请完成所有路径模型的变量配置。";
  const modelNames = payload.models.map((model) => model.name);
  if (new Set(modelNames).size !== modelNames.length) return "路径模型名称不能重复。";
  if (!(payload.inference.alpha > 0 && payload.inference.alpha < 0.5)) return "显著性水平 α 必须在 0 与 0.5 之间。";
  if (!Number.isInteger(payload.inference.bootstrap_samples) || payload.inference.bootstrap_samples < 200 || payload.inference.bootstrap_samples > 20000) return "Bootstrap 次数应为 200 至 20000 的整数。";
  return "";
}

async function handleRunAnalysis() {
  hideAlert(els.analysisError);
  hideAlert(els.jobError);
  if (!state.dataset) {
    showStep("upload");
    showAlert(els.uploadError, "请先导入数据。");
    return;
  }
  const payload = collectAnalysisPayload();
  const validationMessage = validateAnalysis(payload);
  if (validationMessage) {
    showStep("analysis");
    showAlert(els.analysisError, validationMessage);
    return;
  }
  prepareJobView();
  unlockStep("results");
  showStep("results");
  try {
    const raw = await startAnalysis(payload);
    const job = normalizeJobResponse(raw, raw.job_id || raw.id);
    state.jobId = job.id;
    state.runId = job.runId || String(raw.run_id || "");
    if (!state.jobId) throw new Error("任务响应缺少 job_id。");
    rememberJob();
    updateJobView(job);
    if (terminalStatuses.has(job.status)) {
      finishJob(job);
    } else {
      schedulePoll();
    }
  } catch (error) {
    failJob(error.message);
  }
}

function prepareJobView() {
  clearTimeout(state.pollTimer);
  state.seenLogs.clear();
  state.pollFailures = 0;
  state.jobId = null;
  state.runId = null;
  els.jobError.classList.add("is-hidden");
  els.resultWorkspace.classList.add("is-hidden");
  els.rerunButton.classList.add("is-hidden");
  els.jobProgress.classList.remove("is-hidden");
  els.runLog.replaceChildren();
  els.jobBadge.className = "job-badge is-running";
  els.jobBadge.textContent = "创建中";
  setProgress(0, "正在创建分析任务", "准备数据与模型参数");
}

function schedulePoll(delay = 900) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(pollJob, delay);
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const raw = await getJob(state.jobId);
    const job = normalizeJobResponse(raw, state.jobId);
    state.pollFailures = 0;
    updateJobView(job);
    if (terminalStatuses.has(job.status)) finishJob(job);
    else schedulePoll(900);
  } catch (error) {
    state.pollFailures += 1;
    if (/404|不存在|not found/i.test(error.message) && state.runId) {
      await recoverRun();
    } else if (state.pollFailures >= 8) {
      failJob(error.message);
    } else {
      appendLog("连接暂时中断，正在重试", "active");
      schedulePoll(1800);
    }
  }
}

async function recoverRun() {
  try {
    const raw = await getRun(state.runId);
    const job = normalizeJobResponse(raw, state.jobId);
    updateJobView(job);
    if (terminalStatuses.has(job.status)) finishJob(job);
    else schedulePoll(1800);
  } catch (error) {
    failJob(error.message);
  }
}

function updateJobView(job) {
  setProgress(job.progress, job.title || "正在分析", job.message || "模型运行中");
  els.jobBadge.className = "job-badge is-running";
  els.jobBadge.textContent = statusLabel(job.status);
  if (job.logs.length) {
    job.logs.forEach((message) => appendLog(message, "done"));
  } else if (job.message) {
    appendLog(job.message, "active");
  }
}

function finishJob(job) {
  clearTimeout(state.pollTimer);
  if (["failed", "error", "cancelled"].includes(job.status)) {
    failJob(job.error || job.message || "分析任务未完成。");
    return;
  }
  const partial = job.status === "completed_with_errors"
    || (job.summary?.failed_modules || []).length > 0
    || (job.summary?.failed_models || []).length > 0;
  setProgress(
    100,
    partial ? "分析部分完成" : "分析完成",
    job.message || (partial ? "部分模块失败，请查看报告中的错误" : "结果与文件已生成"),
  );
  els.jobBadge.className = partial ? "job-badge is-warning" : "job-badge is-complete";
  els.jobBadge.textContent = partial ? "部分完成" : "已完成";
  appendLog(partial ? "分析完成，但存在模块错误" : "分析完成", "done");
  renderResults(job);
  els.resultWorkspace.classList.remove("is-hidden");
  els.rerunButton.classList.remove("is-hidden");
  showToast(partial ? "分析部分完成，请检查模块错误" : "分析已完成", partial);
}

function failJob(message) {
  clearTimeout(state.pollTimer);
  els.jobBadge.className = "job-badge is-failed";
  els.jobBadge.textContent = "运行失败";
  els.progressTitle.textContent = "分析未完成";
  els.progressMessage.textContent = "请检查设置后重新运行";
  showAlert(els.jobError, message);
  els.rerunButton.classList.remove("is-hidden");
}

function setProgress(value, title, message) {
  const percent = Math.round(Math.max(0, Math.min(100, Number(value) || 0)));
  els.progressTitle.textContent = title;
  els.progressMessage.textContent = message;
  els.progressPercent.textContent = `${percent}%`;
  els.progressBar.style.width = `${percent}%`;
  const track = els.progressBar.parentElement;
  track.setAttribute("aria-valuenow", String(percent));
}

function appendLog(message, status = "done") {
  const text = String(message || "").trim();
  if (!text || state.seenLogs.has(text)) return;
  state.seenLogs.add(text);
  els.runLog.querySelectorAll(".is-active").forEach((item) => item.classList.replace("is-active", "is-done"));
  const item = document.createElement("li");
  item.className = status === "active" ? "is-active" : "is-done";
  item.textContent = text;
  els.runLog.append(item);
}

function renderResults(job) {
  renderStructuredResult(els.resultSummary, job.summary, "分析结果");
  renderStructuredResult(els.resultDiagnostics, job.diagnostics, "模型诊断");
  renderArtifacts(job.artifacts);
  selectResultTab("summary");
}

function renderStructuredResult(container, data, fallbackTitle) {
  container.replaceChildren();
  if (data === null || data === undefined || (Array.isArray(data) && !data.length) || (isPlainObject(data) && !Object.keys(data).length)) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const strong = document.createElement("strong");
    strong.textContent = "暂无可显示内容";
    empty.append(strong);
    container.append(empty);
    return;
  }
  if (!isPlainObject(data)) {
    container.append(buildResultSection(fallbackTitle, data));
    return;
  }
  const entries = Object.entries(data).filter(([key]) => key !== "artifacts" && key !== "diagnostics");
  const scalarEntries = entries.filter(([, value]) => isScalar(value));
  const complexEntries = entries.filter(([, value]) => !isScalar(value));
  if (scalarEntries.length) container.append(buildMetricSection(fallbackTitle, scalarEntries));
  complexEntries.forEach(([key, value]) => {
    if (key === "path_models" && Array.isArray(value)) container.append(buildPathModelsSection(value));
    else container.append(buildResultSection(humanizeKey(key), value));
  });
}

function buildPathModelsSection(models) {
  const section = document.createElement("section");
  section.className = "result-section path-model-results";
  const heading = document.createElement("h3");
  heading.textContent = "回归路径模型";
  const list = document.createElement("div");
  list.className = "path-result-list";
  models.forEach((model, index) => {
    const article = document.createElement("article");
    article.className = "path-result-item";
    const header = document.createElement("div");
    header.className = "path-result-head";
    const title = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = model.name || `路径模型 ${index + 1}`;
    const meta = document.createElement("small");
    const artifactCount = Array.isArray(model.artifacts) ? model.artifacts.length : 0;
    const formula = pathModelFormula(model.config || {});
    meta.textContent = `${model.id || `model-${String(index + 1).padStart(2, "0")}`} · ${analysisTypeLabel(model.analysis)}${formula ? ` · ${formula}` : ""}${artifactCount ? ` · ${artifactCount} 个图表` : ""}`;
    title.append(name, meta);
    const badge = document.createElement("span");
    const failed = model.status === "error";
    badge.className = `path-result-status${failed ? " is-error" : ""}`;
    badge.textContent = failed ? "失败" : "完成";
    header.append(title, badge);
    article.append(header);

    if (model.error) {
      const error = document.createElement("p");
      error.className = "path-result-error";
      error.textContent = model.error;
      article.append(error);
    }
    if (isPlainObject(model.result) && Object.keys(model.result).length) {
      const content = document.createElement("div");
      content.className = "path-result-content";
      const entries = Object.entries(model.result).filter(([key]) => key !== "artifacts");
      const scalar = entries.filter(([, value]) => isScalar(value));
      if (scalar.length) content.append(buildKeyValueTable(scalar));
      entries.filter(([, value]) => !isScalar(value)).forEach(([key, value]) => {
        content.append(buildResultSection(humanizeKey(key), value));
      });
      article.append(content);
    }
    list.append(article);
  });
  section.append(heading, list);
  return section;
}

function analysisTypeLabel(value) {
  const labels = {
    regression: "主效应回归",
    mediation: "中介模型",
    moderation: "调节模型",
    moderated_mediation: "被调节的中介模型",
  };
  return labels[value] || String(value || "路径分析");
}

function pathModelFormula(config) {
  if (!config.x || !config.y) return "";
  const chain = config.mediator ? `${config.x} → ${config.mediator} → ${config.y}` : `${config.x} → ${config.y}`;
  return config.moderator ? `${chain}（W: ${config.moderator}）` : chain;
}

function buildMetricSection(title, entries) {
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const grid = document.createElement("div");
  grid.className = "metric-grid";
  entries.forEach(([key, value]) => {
    const item = document.createElement("div");
    item.className = "metric-item";
    const label = document.createElement("span");
    label.textContent = humanizeKey(key);
    label.title = label.textContent;
    const output = document.createElement("strong");
    output.textContent = formatResultValue(value);
    item.append(label, output);
    grid.append(item);
  });
  section.append(heading, grid);
  return section;
}

function buildResultSection(title, value) {
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  if (Array.isArray(value)) {
    if (value.length && value.every(isPlainObject)) {
      section.append(buildObjectTable(value));
    } else {
      const list = document.createElement("ul");
      list.className = "result-notes";
      value.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = formatResultValue(item);
        list.append(li);
      });
      section.append(list);
    }
  } else if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.every(([, item]) => isScalar(item))) {
      section.append(buildKeyValueTable(entries));
    } else {
      const scalar = entries.filter(([, item]) => isScalar(item));
      if (scalar.length) section.append(buildKeyValueTable(scalar));
      entries.filter(([, item]) => !isScalar(item)).forEach(([key, item]) => section.append(buildResultSection(humanizeKey(key), item)));
    }
  } else {
    const text = document.createElement("p");
    text.textContent = formatResultValue(value);
    section.append(text);
  }
  return section;
}

function buildObjectTable(rows) {
  const keys = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const wrapper = document.createElement("div");
  wrapper.className = "result-table";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  keys.forEach((key) => {
    const th = document.createElement("th");
    th.textContent = humanizeKey(key);
    headerRow.append(th);
  });
  thead.append(headerRow);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    keys.forEach((key) => {
      const td = document.createElement("td");
      td.textContent = formatResultValue(row[key]);
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(thead, tbody);
  wrapper.append(table);
  return wrapper;
}

function buildKeyValueTable(entries) {
  return buildObjectTable(entries.map(([key, value]) => ({ 指标: humanizeKey(key), 结果: formatResultValue(value) })));
}

function renderArtifacts(artifacts) {
  els.artifactList.replaceChildren();
  els.artifactCount.textContent = artifacts.length ? String(artifacts.length) : "";
  if (!artifacts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const strong = document.createElement("strong");
    strong.textContent = "暂无可下载文件";
    empty.append(strong);
    els.artifactList.append(empty);
    return;
  }
  artifacts.forEach((artifact) => {
    const link = document.createElement("a");
    link.className = "artifact-item";
    link.href = artifact.url;
    if (artifact.type === "HTML") {
      link.target = "_blank";
      link.rel = "noopener";
    } else {
      link.download = artifact.filename;
    }
    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.textContent = artifact.type.toUpperCase().slice(0, 5);
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = artifact.name;
    const type = document.createElement("small");
    type.textContent = artifact.type.toUpperCase();
    copy.append(name, type);
    const symbol = document.createElement("span");
    symbol.className = "download-symbol";
    symbol.setAttribute("aria-hidden", "true");
    symbol.textContent = "↓";
    link.append(icon, copy, symbol);
    els.artifactList.append(link);
  });
}

function selectResultTab(name) {
  document.querySelectorAll("[data-result-tab]").forEach((button) => {
    const active = button.dataset.resultTab === name;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-result-panel]").forEach((panel) => {
    const active = panel.dataset.resultPanel === name;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
}

function handleResultTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll("[data-result-tab]")];
  const current = tabs.indexOf(event.currentTarget);
  let next = current;
  if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
  if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
  if (event.key === "Home") next = 0;
  if (event.key === "End") next = tabs.length - 1;
  selectResultTab(tabs[next].dataset.resultTab);
  tabs[next].focus();
}

function showStep(name) {
  const targetButton = document.querySelector(`[data-step-target="${name}"]`);
  if (!stepOrder.includes(name) || targetButton?.disabled) return;
  state.currentStep = name;
  document.querySelectorAll("[data-step]").forEach((panel) => {
    const active = panel.dataset.step === name;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  document.querySelectorAll("[data-step-target]").forEach((button) => {
    const active = button.dataset.stepTarget === name;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function unlockStep(name) {
  const index = stepOrder.indexOf(name);
  stepOrder.slice(0, index + 1).forEach((step, stepIndex) => {
    const button = document.querySelector(`[data-step-target="${step}"]`);
    button.disabled = false;
    button.classList.toggle("is-complete", stepIndex < index);
  });
}

function lockStepsAfter(name) {
  const index = stepOrder.indexOf(name);
  stepOrder.forEach((step, stepIndex) => {
    const button = document.querySelector(`[data-step-target="${step}"]`);
    button.disabled = stepIndex > index;
    button.classList.toggle("is-complete", stepIndex < index);
  });
}

function clearVariableConfiguration() {
  clearTimeout(state.pollTimer);
  state.jobId = null;
  state.runId = null;
  forgetSavedJob();
  state.scaleSequence = 0;
  state.modelSequence = 0;
  els.scaleList.replaceChildren();
  els.pathModelList.replaceChildren();
  els.resultSummary.replaceChildren();
  els.resultDiagnostics.replaceChildren();
  els.artifactList.replaceChildren();
  els.resultWorkspace.classList.add("is-hidden");
  els.rerunButton.classList.add("is-hidden");
  refreshScaleIndices();
  refreshPathModelIndices();
  [els.variableError, els.analysisError, els.jobError].forEach(hideAlert);
}

function updateAnalysisCount() {
  const globalCount = document.querySelectorAll("input[name='analysis']:checked").length;
  const modelCount = els.pathModelList?.querySelectorAll(".path-model-editor").length || 0;
  els.analysisCount.textContent = `${globalCount} 项全局 · ${modelCount} 个路径`;
}

function resetApplication() {
  clearTimeout(state.pollTimer);
  state.file = null;
  state.dataset = null;
  state.jobId = null;
  state.runId = null;
  forgetSavedJob();
  state.pollFailures = 0;
  state.seenLogs.clear();
  state.scaleSequence = 0;
  state.modelSequence = 0;
  els.fileInput.value = "";
  els.fileLabel.textContent = "选择数据文件";
  els.fileMeta.textContent = "CSV 或 XLSX，最大 100 MB";
  els.uploadButton.disabled = true;
  els.uploadState.textContent = "CSV / XLSX";
  els.datasetContext.textContent = "新分析";
  els.dataReview.classList.add("is-hidden");
  els.scaleList.replaceChildren();
  els.pathModelList.replaceChildren();
  refreshScaleIndices();
  refreshPathModelIndices();
  [els.uploadError, els.variableError, els.analysisError, els.jobError].forEach(hideAlert);
  document.querySelectorAll("[data-step-target]").forEach((button, index) => {
    button.disabled = index !== 0;
    button.classList.remove("is-complete");
  });
  showStep("upload");
  showToast("已重置分析");
}

function rememberJob() {
  if (!state.jobId && !state.runId) return;
  sessionStorage.setItem(savedJobKey, JSON.stringify({ jobId: state.jobId, runId: state.runId }));
}

function forgetSavedJob() {
  sessionStorage.removeItem(savedJobKey);
}

async function resumeSavedJob() {
  let saved;
  try {
    saved = JSON.parse(sessionStorage.getItem(savedJobKey) || "null");
  } catch (_error) {
    forgetSavedJob();
    return;
  }
  if (!saved?.jobId && !saved?.runId) return;
  prepareJobView();
  state.jobId = String(saved.jobId || "");
  state.runId = String(saved.runId || "");
  unlockStep("results");
  showStep("results");
  try {
    const raw = state.jobId ? await getJob(state.jobId) : await getRun(state.runId);
    const job = normalizeJobResponse(raw, state.jobId);
    updateJobView(job);
    if (terminalStatuses.has(job.status)) finishJob(job);
    else schedulePoll();
  } catch (error) {
    if (state.runId) await recoverRun();
    else failJob(error.message);
  }
}

function selectedOptions(select) {
  return [...select.selectedOptions].map((option) => option.value);
}

function showAlert(element, message) {
  element.textContent = String(message);
  element.classList.remove("is-hidden");
}

function hideAlert(element) {
  element.textContent = "";
  element.classList.add("is-hidden");
}

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " is-error" : ""}`;
  toast.textContent = message;
  els.toastRegion.append(toast);
  setTimeout(() => toast.remove(), 3200);
}

function normalizeMessages(value) {
  if (!value) return [];
  const entries = Array.isArray(value) ? value : [value];
  return entries.map((item) => {
    if (typeof item === "string") return item;
    return String(item.message || item.detail || item.label || item.stage || JSON.stringify(item));
  });
}

function firstNumber(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "" && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

function formatInteger(value) {
  return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${number < 0.1 && number > 0 ? number.toFixed(2) : number.toFixed(1)}%`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function fileExtension(path) {
  const cleanPath = String(path || "").split("?")[0];
  const extension = cleanPath.includes(".") ? cleanPath.split(".").pop() : "FILE";
  return String(extension || "FILE").toUpperCase();
}

function inferredProgress(status) {
  if (["queued", "pending", "created"].includes(status)) return 5;
  if (["completed", "complete", "succeeded", "success", "done"].includes(status)) return 100;
  return 35;
}

function statusLabel(status) {
  const labels = {
    queued: "排队中", pending: "等待中", created: "已创建", running: "运行中", processing: "处理中",
    completed: "已完成", complete: "已完成", succeeded: "已完成", success: "已完成", done: "已完成",
    completed_with_errors: "部分完成",
    failed: "失败", error: "错误", cancelled: "已取消",
  };
  return labels[status] || status || "运行中";
}

function humanizeKey(key) {
  const labels = {
    cfa: "验证性因子分析（CFA）", harman: "Harman 单因子检验", ulmc: "ULMC 检验",
    descriptives: "描述性统计", correlations: "相关分析", correlation: "相关分析",
    regression: "回归分析", mediation: "中介效应", moderation: "调节效应",
    moderated_mediation: "被调节的中介效应", model_fit: "模型拟合", reliability: "信度",
    validity: "效度", coefficients: "回归系数", indirect_effect: "间接效应", direct_effect: "直接效应",
    total_effect: "总效应", estimate: "估计值", std_error: "标准误", se: "标准误", p_value: "p 值",
    p: "p 值", ci_lower: "置信区间下限", ci_upper: "置信区间上限", lower: "下限", upper: "上限",
    variable: "变量", term: "变量", mean: "均值", std: "标准差", n: "样本量", r_squared: "R²",
    adj_r_squared: "调整 R²", interpretation: "结论", warnings: "提示", notes: "备注",
    input_rows: "输入样本量", completed_modules: "已完成模块", failed_modules: "失败模块",
    path_models: "路径模型", completed_models: "已完成路径", failed_models: "失败路径",
    id: "模型编号", name: "模型名称", analysis: "模型类型", status: "运行状态", error: "错误",
    cfa_fit: "CFA 模型拟合", harman_first_component_percent: "Harman 第一因子解释率",
    mediation_indirect: "中介间接效应", moderation_interaction: "调节交互项",
    moderated_mediation_index: "被调节的中介指数", ci_low: "置信区间下限", ci_high: "置信区间上限",
    path_models: "回归路径模型", model_id: "模型编号", model_name: "模型名称", model_type: "模型类型",
  };
  const normalized = String(key).toLowerCase();
  return labels[normalized] || String(key).replaceAll("_", " ");
}

function formatResultValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return value.toLocaleString("zh-CN");
    if (Math.abs(value) < 0.001 && value !== 0) return value.toExponential(3);
    return value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isScalar(value) {
  return value === null || value === undefined || ["string", "number", "boolean"].includes(typeof value);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
