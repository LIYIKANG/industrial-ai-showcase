let currentOntology = null;
let modelEdited = false;
let modelRevision = 0;
let solveSubmitting = false;
let currentResult = null;
let currentValidation = null;
let baselineResult = null;
let chemScenarios = [];
let currentProjectId = null;
let activeTaskId = null;
let taskTimer = null;
let draftTimer = null;
let currentProjectVersions = [];

const $ = (id) => document.getElementById(id);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
}[char]));
const pretty = (value) => JSON.stringify(value, null, 2);

const graphState = {
  selectedId: null,
  hidden: new Set(),
  relation: "",
  neighborhoodOnly: false,
  positions: new Map(),
};
let cy = null;  // cytoscape instance, lazily created

async function parseResponse(response) {
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

async function getJson(url) {
  const response = await fetch(url);
  const data = await parseResponse(response);
  if (!response.ok) throw new Error(data?.detail || data?.error || String(data));
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await parseResponse(response);
  if (!response.ok) throw new Error(data?.detail || data?.error || String(data));
  return data;
}

function providerConfig() {
  return {
    provider: $("providerName").value,
    model: $("modelName").value.trim() || null,
    api_key: $("providerName").value === "deepseek" ? $("apiKey").value.trim() || null : null,
  };
}

function problem() {
  return currentOntology?.problem || currentOntology?.solver_input || null;
}

function setTask(stage, detail = "", progress = 0, cancellable = false) {
  $("taskStage").textContent = stage;
  $("taskDetail").textContent = detail;
  $("taskProgress").style.width = `${Math.max(0, Math.min(100, progress))}%`;
  $("cancelTask").disabled = !cancellable;
}

function setView(id) {
  $$(".viewTab").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
  $$(".viewPane").forEach((pane) => pane.classList.toggle("active", pane.id === id));
  if (id === "systemView") renderSystemOverview();
  if (id === "graphView") drawGraph();
  if (id === "compareView") renderCompare();
  if (id === "resultView" && currentResult) renderCompare();
}

function renderTable(id, rows, onSelect = null) {
  const table = $(id);
  const data = Array.isArray(rows) ? rows : [];
  if (!data.length) {
    table.innerHTML = "<tbody><tr><td>暂无数据</td></tr></tbody>";
    return;
  }
  const columns = Array.from(new Set(data.flatMap((row) => Object.keys(row))));
  table.innerHTML = `<thead><tr>${columns.map((column) => `<th>${esc(column)}</th>`).join("")}</tr></thead>
    <tbody>${data.map((row, index) => `<tr data-row-index="${index}">${columns.map((column) => {
      const value = row[column];
      return `<td>${esc(typeof value === "object" && value !== null ? JSON.stringify(value) : value)}</td>`;
    }).join("")}</tr>`).join("")}</tbody>`;
  if (onSelect) {
    Array.from(table.querySelectorAll("tbody tr")).forEach((row) => {
      row.onclick = () => onSelect(data[Number(row.dataset.rowIndex)]);
    });
  }
}

function showRowInspector(title, row) {
  $("inspectorTitle").textContent = title;
  $("inspectorBody").innerHTML = Object.entries(row || {}).map(([key, value]) => `
    <div class="inspectField"><span>${esc(key)}</span><b>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</b></div>
  `).join("") || "暂无数据";
}

function renderOntologyTables() {
  const ontology = currentOntology || {};
  const tables = ontology.tables || {};
  renderTable("ontologyEntities", ontology.entities || [], (row) => showRowInspector(row["实体名称"] || "实体明细", row));
  renderTable("ontologyAttributes", ontology.attributes || [], (row) => showRowInspector(row["属性名称"] || "属性明细", row));
  renderTable("ontologyRelationships", ontology.relationships || [], (row) => showRowInspector(row["关系"] || "关系明细", row));
  renderTable("ontologyVariables", tables.variables || [], (row) => showRowInspector(row.name || row.id || "决策变量", row));
  renderTable("ontologyParameters", tables.parameters || [], (row) => showRowInspector(row.name || row.id || "模型参数", row));
  renderTable("ontologyConstraints", tables.constraints || [], (row) => showRowInspector(row.name || row.id || "约束条件", row));
  const audit = ontology.ontology_audit || {};
  $("ontologyAudit").innerHTML = Object.entries(audit).map(([key, value], index) => `
    <div class="auditItem ${index === 0 ? "emphasis" : ""}">
      <span>${esc(key)}</span>
      <b>${esc(typeof value === "object" ? JSON.stringify(value, null, 2) : value)}</b>
    </div>
  `).join("") || `<div class="validationEmpty">暂无审计数据</div>`;
  $("ontologySummary").innerHTML = [
    ["实体", (ontology.entities || []).length],
    ["属性明细", (ontology.attributes || []).length],
    ["关系", (ontology.relationships || []).length],
    ["完整性", audit["完整性评分"] == null ? "-" : `${audit["完整性评分"]}%`],
  ].map(([label, value]) => `<span>${label} <b>${esc(value)}</b></span>`).join("");
}

function setText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function countRows(value) {
  return Array.isArray(value) ? value.length : null;
}

function modelCounts() {
  const ontology = currentOntology || {};
  const tables = ontology.tables || {};
  const model = problem() || {};
  const summary = ontology.summary || {};
  return {
    entities: countRows(ontology.entities) ?? 0,
    attributes: countRows(ontology.attributes) ?? 0,
    relationships: countRows(ontology.relationships) ?? 0,
    variables: summary.variable_count ?? countRows(model.decision_variables) ?? countRows(tables.variables) ?? 0,
    constraints: summary.constraint_count ?? countRows(model.constraints) ?? countRows(tables.constraints) ?? 0,
    parameters: countRows(model.parameters) ?? countRows(tables.parameters) ?? 0,
  };
}

function renderSystemOverview() {
  const ontology = currentOntology || {};
  const audit = ontology.ontology_audit || {};
  const counts = modelCounts();
  const hasProblem = Boolean($("problemText")?.value.trim());
  const hasModel = Boolean(problem());
  const provider = $("providerName")?.value === "deepseek" ? "DeepSeek 云端" : "Ollama 本地";
  const modelName = $("modelName")?.value.trim() || "-";
  const cloudMode = $("providerName")?.value === "deepseek";
  const completeness = audit["完整性评分"];
  const validationText = currentValidation ? (currentValidation.valid ? "模型已校验，可求解" : "模型未通过校验") : "等待模型校验";
  const solveText = currentResult
    ? `${currentResult.status || "unknown"} · 目标值 ${currentResult.objective_value ?? "-"}`
    : "尚未执行严格求解";

  $("systemHeroStats").innerHTML = [
    ["实体", counts.entities],
    ["关系", counts.relationships],
    ["变量", counts.variables],
    ["约束", counts.constraints],
    ["完整性", completeness == null ? "-" : `${completeness}%`],
  ].map(([label, value]) => `<span><b>${esc(value)}</b><em>${esc(label)}</em></span>`).join("");

  setText("flowProblemStatus", hasProblem ? "已获得业务问题，可继续抽取对象、目标和约束" : "等待输入业务问题或上传文件");
  setText("flowLlmStatus", `${provider} · ${modelName}，用于语义理解和建模初稿`);
  setText("flowOntologyStatus", hasModel ? `实体 ${counts.entities}，属性 ${counts.attributes}，关系 ${counts.relationships}` : "等待 AI 划分本体");
  setText("flowMathStatus", hasModel ? `变量 ${counts.variables}，参数 ${counts.parameters}，约束 ${counts.constraints}` : "等待形成可计算数学模型");
  setText("flowSolverStatus", currentValidation?.valid ? "模型可求解，可调用本地 HiGHS" : validationText);
  setText("flowResultStatus", currentResult ? "已生成严格求解结果，可导出或用于方案对比" : "求解后生成方案、目标值和约束检查");

  setText("llmRelationProvider", `当前：${provider} / ${modelName}`);
  setText("llmRelationPrivacy", cloudMode ? "DeepSeek API 会把问题文本发送到云端" : "Ollama 模式下建模文本留在本机");
  setText("ontologyRelationCount", `实体 ${counts.entities}，属性 ${counts.attributes}，关系 ${counts.relationships}`);
  setText("ontologyRelationAudit", completeness == null ? "完整性待评估" : `完整性评分 ${completeness}%`);
  setText("mathRelationCount", `变量 ${counts.variables}，参数 ${counts.parameters}，约束 ${counts.constraints}`);
  setText("mathRelationStatus", validationText);
  setText("solverRelationStatus", solveText);

  const nextStep = !hasProblem
    ? "先加载示例或输入客户问题，再调用 AI 划分本体并建立数学模型。"
    : !hasModel
      ? "下一步：点击左侧按钮，让大模型抽取本体并生成数学模型。"
      : !currentValidation
        ? "下一步：校验数学模型，确认变量引用、上下界和约束结构。"
        : !currentValidation.valid
          ? "下一步：进入数学模型页修正校验错误。"
          : !currentResult
            ? "下一步：调用本地 HiGHS 做严格求解。"
            : "下一步：查看结果、做方案对比，或导出 Excel / GraphML / Cypher。";
  setText("systemNextStep", nextStep);
}

function updateMetrics() {
  const summary = currentOntology?.summary || {};
  $("metricVariables").textContent = summary.variable_count ?? "-";
  $("metricConstraints").textContent = summary.constraint_count ?? "-";
  $("metricValidation").textContent = currentValidation ? (currentValidation.valid ? "通过" : "未通过") : "-";
  $("metricSolve").textContent = currentResult?.status || "-";
  $("metricObjective").textContent = currentResult?.objective_value ?? "-";
  renderSystemOverview();
}

function applyOntology(ontology) {
  modelEdited = false;
  modelRevision += 1;
  currentOntology = ontology;
  currentResult = null;
  currentValidation = null;
  graphState.positions.clear();
  graphState.selectedId = null;
  graphState.neighborhoodOnly = false;
  $("modelEditor").value = pretty(problem());
  renderOntologyTables();
  renderValidation(null);
  updateMetrics();
  renderGraphControls();
  drawGraph(!loadGraphLayout());
  renderResults();
  scheduleDraftSave();
  setTask(
    ontology.is_preview ? "示例模型已加载" : "建模完成",
    ontology.is_preview ? "可直接校验和本地求解，也可以调用 AI 重新建模。" : `模型来源：${ontology.llm?.provider || "人工"}`,
    100,
  );
}

function renderValidation(validation) {
  currentValidation = validation;
  const badge = $("validationBadge");
  if (!validation) {
    badge.className = "statusBadge neutral";
    badge.textContent = "未校验";
    $("validationList").innerHTML = `<div class="validationEmpty">应用或修改模型后执行校验。</div>`;
    updateMetrics();
    return;
  }
  badge.className = `statusBadge ${validation.valid ? "ok" : "error"}`;
  badge.textContent = validation.valid ? "可求解" : "需修正";
  const items = [
    ...(validation.errors || []).map((item) => ({ ...item, tone: "error" })),
    ...(validation.warnings || []).map((item) => ({ ...item, tone: "warn" })),
  ];
  $("validationList").innerHTML = items.length
    ? items.map((item) => `<div class="validationItem ${item.tone === "error" ? "error" : ""}">
        <b>${esc(item.path)}</b><br>${esc(item.message)}
      </div>`).join("")
    : `<div class="validationEmpty">结构、变量引用和约束均通过检查。</div>`;
  updateMetrics();
}

async function validateCurrent(showTask = true) {
  if (!problem()) throw new Error("请先建立数学模型。");
  if (showTask) setTask("校验模型", "检查变量、目标函数、上下界和约束引用。", 35);
  const validation = await postJson("/api/model/validate", { problem: problem() });
  renderValidation(validation);
  if (showTask) {
    setTask(validation.valid ? "模型校验通过" : "模型校验失败", `${validation.counts.errors} 个错误，${validation.counts.warnings} 个警告。`, 100);
  }
  return validation;
}

function updateProviderUI() {
  const cloud = $("providerName").value === "deepseek";
  $("apiKeyBox").hidden = !cloud;
  $("privacyText").textContent = cloud
    ? "DeepSeek 模式会将问题文本发送到外部 API。数学求解和项目存储仍在本机完成。"
    : "HiGHS 求解和项目存储均在本机完成。Ollama 模式下，客户数据不离开电脑。";
  renderSystemOverview();
}

async function loadProviders() {
  const data = await getJson("/api/ai/providers");
  const selected = data.providers.find((item) => item.id === $("providerName").value);
  if (!selected) return;
  $("modelName").value = selected.default_model;
  $("modelOptions").innerHTML = (selected.models || []).map((model) => `<option value="${esc(model)}"></option>`).join("");
}

async function checkProvider() {
  try {
    $("connectionText").textContent = "检查中";
    const data = await postJson("/api/ai/status", providerConfig());
    $("connectionText").textContent = data.connected
      ? `${data.provider} · ${data.selected_model} · 已连接`
      : `${data.provider} · 未连接`;
    if (data.models?.length) {
      $("modelOptions").innerHTML = data.models.map((model) => `<option value="${esc(model)}"></option>`).join("");
    }
    setTask(data.connected ? "模型连接正常" : "模型连接失败", data.error || data.hint || data.base_url, 100);
  } catch (error) {
    $("connectionText").textContent = "检查失败";
    setTask("模型连接失败", error.message, 100);
  }
}

let loadingExample = false;
function lockExampleControls(locked) {
  ["loadExample", "solveBtn", "solveFromSystem", "analyzeBtn"].forEach(id => { $(id).disabled = locked; });
}
async function loadExample() {
  if (loadingExample) return;
  loadingExample = true;
  lockExampleControls(true);
  try {
    const data = await getJson("/api/generic/example");
    $("domainInput").value = data.domain || "";
    $("objectiveHint").value = "最大化总利润";
    $("problemText").value = data.text || "";
    applyOntology(data.preview);
    await validateCurrent(false);
    setView("graphView");
  } finally {
    loadingExample = false;
    lockExampleControls(false);
  }
}

async function startTask(url, payload, onComplete) {
  const task = await postJson(url, payload);
  activeTaskId = task.id;
  setTask(task.stage, "任务已进入本地后台队列。", task.progress, true);
  clearInterval(taskTimer);
  taskTimer = setInterval(async () => {
    try {
      const state = await getJson(`/api/tasks/${activeTaskId}`);
      setTask(state.stage, state.error || `${state.kind} · ${state.status}`, state.progress, ["queued", "running"].includes(state.status));
      if (state.status === "completed") {
        clearInterval(taskTimer);
        activeTaskId = null;
        await onComplete(state.result);
      } else if (["failed", "cancelled"].includes(state.status)) {
        clearInterval(taskTimer);
        activeTaskId = null;
      }
    } catch (error) {
      clearInterval(taskTimer);
      activeTaskId = null;
      setTask("任务状态读取失败", error.message, 100);
    }
  }, 700);
}

async function analyzeProblem() {
  const text = $("problemText").value.trim();
  if (!text) throw new Error("请输入业务问题。");
  const file = $("fileInput").files[0];
  if (file) {
    setTask("上传并建模", file.name, 10);
    const form = new FormData();
    form.append("file", file);
    form.append("domain", $("domainInput").value.trim());
    form.append("objective_hint", $("objectiveHint").value.trim());
    const config = providerConfig();
    form.append("provider", config.provider);
    if (config.model) form.append("model", config.model);
    if (config.api_key) form.append("api_key", config.api_key);
    const response = await fetch("/api/generic/upload-analyze", { method: "POST", body: form });
    const data = await parseResponse(response);
    if (!response.ok) throw new Error(data?.detail || "文件建模失败。");
    $("problemText").value = data.text;
    applyOntology(data.ontology);
    await validateCurrent(false);
    return;
  }
  await startTask("/api/tasks/analyze", {
    text,
    domain: $("domainInput").value.trim() || null,
    objective_hint: $("objectiveHint").value.trim() || null,
    ...providerConfig(),
  }, async (ontology) => {
    applyOntology(ontology);
    await validateCurrent(false);
  });
}

async function applyModelEditor() {
  let edited;
  try {
    edited = JSON.parse($("modelEditor").value);
  } catch (error) {
    throw new Error(`JSON 格式错误：${error.message}`);
  }
  const editRevision = modelRevision;
  const normalized = await postJson("/api/model/normalize", { problem: edited });
  if (editRevision !== modelRevision) throw new Error("模型在校验期间已修改，请重新应用或求解。");
  currentOntology = normalized;
  modelEdited = false;
  modelRevision += 1;
  $("modelEditor").value = pretty(problem());
  const appliedRevision = modelRevision;
  currentResult = null;
  graphState.positions.clear();
  renderOntologyTables();
  renderGraphControls();
  drawGraph(true);
  renderResults();
  await validateCurrent();
  if (appliedRevision !== modelRevision) {
    currentValidation = null;
    renderValidation(null);
    updateMetrics();
    throw new Error("模型在校验期间已修改，请重新应用或求解。");
  }
}

async function solveProblem() {
  if (solveSubmitting || activeTaskId) {
    setTask("任务正在执行", "请等待当前计算结束后再求解。", 5);
    return;
  }
  solveSubmitting = true;
  try {
    // Solving must use the visible draft, never silently fall back to the old model.
    if (modelEdited) await applyModelEditor();
    const revision = modelRevision;
    const validation = await validateCurrent();
    if (revision !== modelRevision) {
      currentValidation = null;
      updateMetrics();
      setTask("模型已修改，请重新求解", "请使用当前编辑内容再次求解。", 0);
      return;
    }
    if (!validation.valid) {
      setView("modelView");
      return;
    }
    setTask("准备本地求解", "数据不会发送给大模型或第三方求解平台。", 5);
    await startTask("/api/tasks/solve", {
      problem: problem(),
      time_limit: Number($("timeLimit").value || 60),
    }, async (result) => {
      if (revision !== modelRevision) {
        setTask("模型已修改，请重新求解", "旧计算结果不会覆盖当前模型。", 100);
        return;
      }
      currentResult = result;
      renderResults();
      updateMetrics();
      setView("resultView");
    });
  } finally {
    solveSubmitting = false;
  }
}

function renderBars(id, rows, valueKey, colorClass = "") {
  const container = $(id);
  const values = rows.map((row) => Math.abs(Number(row[valueKey] || 0)));
  const max = Math.max(1, ...values);
  container.className = `barChart ${colorClass}`;
  container.innerHTML = rows.length ? rows.map((row, index) => {
    const value = Number(row[valueKey] || 0);
    return `<div class="barRow">
      <label title="${esc(row.name || row.constraint || row.id)}">${esc(row.name || row.constraint || row.id)}</label>
      <div class="barTrack"><i style="width:${Math.max(2, values[index] / max * 100)}%"></i></div>
      <b>${esc(Number.isFinite(value) ? value.toFixed(2) : "-")}</b>
    </div>`;
  }).join("") : `<div class="validationEmpty">暂无数据</div>`;
}

function renderResults() {
  const banner = $("resultBanner");
  if (!currentResult) {
    banner.className = "resultBanner neutral";
    banner.innerHTML = `<div><strong>尚未执行严格求解</strong><span>校验数学模型后，调用本地 HiGHS。</span></div>`;
    renderBars("variableChart", [], "value");
    renderBars("constraintChart", [], "slack", "constraintChart");
    renderTable("variableTable", []);
    renderTable("constraintTable", []);
    $("rawResult").textContent = "";
    renderCompare();
    return;
  }
  const ok = ["optimal", "limit_reached"].includes(currentResult.status);
  banner.className = `resultBanner ${ok ? "ok" : "error"}`;
  banner.innerHTML = `<div>
    <strong>${esc(currentResult.status)} · ${esc(currentResult.engine || "本地求解器")}</strong>
    <span>${currentResult.strict_solution ? "严格数学求解" : "未获得严格解"} · ${esc(currentResult.solver_family || "")} · ${currentResult.elapsed_seconds ?? "-"} 秒</span>
  </div><b>目标值 ${esc(currentResult.objective_value ?? "-")}</b>`;
  renderBars("variableChart", currentResult.variables || [], "value");
  renderBars("constraintChart", currentResult.constraints_check || [], "slack", "constraintChart");
  renderTable("variableTable", currentResult.variables || []);
  renderTable("constraintTable", currentResult.constraints_check || []);
  $("rawResult").textContent = pretty(currentResult);
  renderCompare();
}

function renderCompare() {
  // Multi-scenario mode: if the project has saved scenarios (from the chem
  // bridge), render them as a side-by-side comparison.  Otherwise fall
  // back to the legacy baseline-vs-current diff.
  if (chemScenarios && chemScenarios.length >= 2) {
    renderMultiScenarioCompare(chemScenarios);
    return;
  }
  const current = currentResult;
  const baseline = baselineResult;
  if (!baseline || !current) {
    $("compareSummary").innerHTML = `<div class="validationEmpty">需要一个基线方案和一个当前方案（或运行 /api/atp/scenarios 生成多场景）。</div>`;
    renderTable("compareTable", []);
    return;
  }
  const delta = Number(current.objective_value || 0) - Number(baseline.objective_value || 0);
  $("compareSummary").innerHTML = `
    <div class="compareMetric"><span>基线目标值</span><b>${esc(baseline.objective_value ?? "-")}</b></div>
    <div class="compareMetric"><span>当前目标值</span><b>${esc(current.objective_value ?? "-")}</b></div>
    <div class="compareMetric"><span>变化</span><b>${delta >= 0 ? "+" : ""}${delta.toFixed(2)}</b></div>`;
  const baseMap = new Map((baseline.variables || []).map((item) => [item.id, item]));
  const rows = (current.variables || []).map((item) => {
    const oldValue = Number(baseMap.get(item.id)?.value || 0);
    return {
      变量: item.name || item.id,
      基线: oldValue,
      当前: item.value,
      变化: Number(item.value || 0) - oldValue,
      单位: item.unit || "",
    };
  });
  renderTable("compareTable", rows);
}

function renderMultiScenarioCompare(scenarios) {
  const names = scenarios.map((s) => s.name || "(未命名)");
  const baseline = scenarios[0].result || {};
  $("compareSummary").innerHTML = scenarios.map((s) => {
    const r = s.result || {};
    return `<div class="compareMetric"><span>${esc(s.name || "-")}</span><b>${esc(r.objective_value ?? "-")}</b><small>${esc(r.status || "")} · ${esc(r.engine || "")} · ${(r.elapsed_seconds ?? "-")}s</small></div>`;
  }).join("");

  const metrics = [
    (s) => Number(s.result?.objective_value || 0),
    (s) => (s.result?.alerts || []).length,
    (s) => Number(s.result?.metadata?.summary?.orders_promised || 0),
    (s) => Number(s.result?.metadata?.summary?.orders_short || 0),
    (s) => Number(s.result?.metadata?.ots?.overall || 0),
    (s) => Number(s.result?.variables?.length || 0),
    (s) => Number(s.result?.constraints_check?.length || 0),
    (s) => Number(s.result?.elapsed_seconds || 0),
  ];
  const metricLabels = ["目标值", "预警数", "承诺订单", "缺料订单", "OTS%", "ATP 变量", "物料约束", "耗时(秒)"];
  const header = ["指标", ...names, "基线→当前变化"].map((h) => `<th>${esc(h)}</th>`).join("");
  const body = metrics.map((fn, idx) => {
    const cells = scenarios.map((s) => {
      const v = fn(s);
      return Number.isFinite(v) ? v.toFixed(2) : "-";
    });
    const baseVal = fn(scenarios[0]);
    const lastVal = fn(scenarios[scenarios.length - 1]);
    const delta = lastVal - baseVal;
    const deltaStr = (delta >= 0 ? "+" : "") + delta.toFixed(2);
    return `<tr><td><strong>${esc(metricLabels[idx])}</strong></td>${cells.map((c) => `<td>${esc(c)}</td>`).join("")}<td>${esc(deltaStr)}</td></tr>`;
  }).join("");
  const allMids = new Set();
  for (const s of scenarios) {
    for (const m of (s.result?.metadata?.materials || [])) allMids.add(m.material_id);
  }
  const matRows = [...allMids].sort().slice(0, 20).map((mid) => {
    const cells = scenarios.map((s) => {
      const m = (s.result?.metadata?.materials || []).find((mm) => mm.material_id === mid);
      if (!m) return "-";
      return `${m.orders_promised || 0}/${m.orders_short || 0}`;
    });
    return `<tr><td><code>${esc(mid)}</code></td>${cells.map((c) => `<td>${esc(c)}</td>`).join("")}<td>-</td></tr>`;
  }).join("");
  $("compareTable").innerHTML = `<thead><tr>${header}</tr></thead>
    <tbody>
      <tr><td colspan="${scenarios.length + 2}" style="background:var(--panel-2);font-weight:600">📊 关键指标</td></tr>
      ${body}
      <tr><td colspan="${scenarios.length + 2}" style="background:var(--panel-2);font-weight:600">📦 前 20 个物料 (承诺/缺料)</td></tr>
      ${matRows}
    </tbody>`;
}

function renderGraphControls() {
  const graph = currentOntology?.graph;
  if (!graph) {
    $("graphStats").textContent = "尚未生成图谱";
    $("graphFilters").innerHTML = "";
    return;
  }
  const hub = graph.hubs?.[0];
  $("graphStats").textContent = `${graph.stats.node_count} 节点 · ${graph.stats.edge_count} 关系 · ${graph.stats.community_count} 社区${hub ? ` · 核心 ${hub.label}` : ""}`;
  $("graphFilters").innerHTML = graph.communities.map((community) => `
    <button class="graphFilter ${graphState.hidden.has(community.category) ? "" : "active"}"
      data-category="${esc(community.category)}" style="--node-color:${community.color}">
      ${esc(community.label)} ${community.node_count}
    </button>`).join("");
  $$("#graphFilters .graphFilter").forEach((button) => {
    button.onclick = () => {
      const category = button.dataset.category;
      if (graphState.hidden.has(category)) graphState.hidden.delete(category);
      else graphState.hidden.add(category);
      renderGraphControls();
      drawGraph(true);
    };
  });
  const relations = Array.from(new Set(graph.edges.map((edge) => edge.relation))).sort();
  $("relationFilter").innerHTML = `<option value="">全部关系</option>${relations.map((relation) => `<option value="${esc(relation)}">${esc(relation)}</option>`).join("")}`;
  $("relationFilter").value = graphState.relation;
}

function ensureCytoscape() {
  if (cy) return cy;
  if (typeof cytoscape === "undefined") {
    $("graphSurface").innerHTML = `<div class="emptyState">图谱库未加载，请检查网络。</div>`;
    return null;
  }
  cy = cytoscape({
    container: $("graphSurface"),
    wheelSensitivity: 0.25,
    minZoom: 0.4,
    maxZoom: 2.4,
    style: [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          "label": "data(label)",
          "color": "#1f2937",
          "font-size": 11,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 6,
          "width": "data(size)",
          "height": "data(size)",
          "border-width": 1.5,
          "border-color": "#ffffff",
          "text-max-width": "120px",
          "text-wrap": "ellipsis",
        },
      },
      { selector: "node:selected", style: {
          "border-width": 3,
          "border-color": "#1976d2",
          "overlay-color": "#1976d2",
          "overlay-opacity": 0.15,
        } },
      { selector: "node.dimmed", style: { "opacity": 0.25 } },
      { selector: "edge", style: {
          "width": 1.4,
          "line-color": "#82909d",
          "target-arrow-color": "#82909d",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "label": "data(relation)",
          "font-size": 10,
          "color": "#5b6573",
          "text-rotation": "autorotate",
          "text-background-color": "#fbfcfd",
          "text-background-opacity": 0.7,
          "text-background-padding": 2,
        } },
      { selector: 'edge[confidence = "EXTRACTED"]', style: { "line-style": "solid" } },
      { selector: 'edge[confidence = "INFERRED"], edge[confidence = "AMBIGUOUS"]',
        style: { "line-style": "dashed" } },
      { selector: "edge.dimmed", style: { "opacity": 0.15 } },
    ],
  });
  cy.on("tap", "node", (event) => {
    graphState.selectedId = event.target.id();
    applyGraphDimming();
    showInspector(graphState.selectedId);
  });
  cy.on("tap", (event) => {
    if (event.target === cy) {
      graphState.selectedId = null;
      applyGraphDimming();
      $("inspectorTitle").textContent = "节点详情";
      $("inspectorBody").textContent = "点击图谱节点查看属性和邻接关系。";
    }
  });
  cy.on("dragfree", "node", (event) => {
    const n = event.target;
    graphState.positions.set(n.id(), n.position());
  });
  return cy;
}

function applyGraphDimming() {
  if (!cy) return;
  const query = $("graphSearch").value.trim().toLowerCase();
  const selectedId = graphState.selectedId;
  const selectedNode = selectedId ? cy.getElementById(selectedId) : null;
  const neighborIds = selectedNode && selectedNode.length
    ? new Set([selectedId, ...selectedNode.neighborhood().map((n) => n.id())])
    : null;
  cy.batch(() => {
    cy.nodes().forEach((node) => {
      const label = String(node.data("label") || "").toLowerCase();
      const type = String(node.data("type") || "").toLowerCase();
      const matchesQuery = !query || label.includes(query) || type.includes(query);
      const isNeighbor = !neighborIds || neighborIds.has(node.id());
      const dimmed = !matchesQuery
        || (selectedId && !isNeighbor)
        || (graphState.neighborhoodOnly && !isNeighbor);
      node.toggleClass("dimmed", dimmed);
    });
    cy.edges().forEach((edge) => {
      const isRelated = !selectedId || edge.source().id() === selectedId || edge.target().id() === selectedId;
      edge.toggleClass("dimmed", !!selectedId && !isRelated);
    });
  });
}
function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);
}

function drawGraph(reset = false) {
  const graph = currentOntology?.graph;
  const surface = $("graphSurface");
  if (!graph) {
    surface.innerHTML = `<div class="emptyState">建立本体后显示知识图谱</div>`;
    if (cy) { cy.destroy(); cy = null; }
    return;
  }
  const cyInst = ensureCytoscape();
  if (!cyInst) return;

  // Build elements from the ontology graph payload.
  const maxDegree = Math.max(1, ...graph.nodes.map((node) => node.degree || 0));
  const elements = [];
  for (const node of graph.nodes) {
    const size = 20 + 13 * Math.sqrt((node.degree || 0) / maxDegree);
    elements.push({
      group: "nodes",
      data: {
        category: node.category,
        id: node.id,
        label: node.label,
        color: node.color,
        type: node.type,
        size,
      },
    });
  }
  for (const edge of graph.edges) {
    elements.push({
      group: "edges",
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        relation: edge.relation,
        confidence: edge.confidence,
        structural: !!edge.structural,
      },
    });
  }
  cyInst.elements().remove();
  cyInst.add(elements);

  // Category visibility filter.
  for (const cat of graphState.hidden) {
    cyInst.nodes(`[category = "${cssEscape(cat)}"]`).hide();
  }

  // Relation filter.
  if (graphState.relation) {
    cyInst.edges().forEach((edge) => {
      if (edge.data("relation") !== graphState.relation) edge.hide();
    });
  }

  // Neighborhood filter.
  if (graphState.neighborhoodOnly && graphState.selectedId) {
    const sel = cyInst.getElementById(graphState.selectedId);
    if (sel && sel.length) {
      const keep = sel.closedNeighborhood();
      cyInst.elements().difference(keep).hide();
    }
  }

  // Layout: use saved positions if available, else cose.
  const haveSaved = !reset && graphState.positions.size > 0
    && graph.nodes.every((node) => graphState.positions.has(node.id));
  if (haveSaved) {
    for (const node of graph.nodes) {
      const pos = graphState.positions.get(node.id);
      cyInst.getElementById(node.id).position(pos);
    }
  } else {
    const layout = cyInst.layout({
      name: "cose",
      animate: false,
      fit: true,
      padding: 30,
      nodeRepulsion: () => 8000,
      idealEdgeLength: () => 90,
    });
    layout.run();
    cyInst.nodes().forEach((node) => {
      graphState.positions.set(node.id(), node.position());
    });
  }

  applyGraphDimming();
  cyInst.fit(undefined, 30);
  if (graphState.selectedId) showInspector(graphState.selectedId);
}

function showInspector(nodeId) {
  const graph = currentOntology?.graph;
  const node = graph?.nodes.find((item) => item.id === nodeId);
  if (!node) return;
  const lookup = new Map(graph.nodes.map((item) => [item.id, item]));
  const links = graph.edges.filter((edge) => edge.source === nodeId || edge.target === nodeId);
  $("inspectorTitle").textContent = node.label;
  $("inspectorBody").innerHTML = `
    <div class="inspectField"><span>类别</span><b>${esc(node.community_name)}</b></div>
    <div class="inspectField"><span>类型</span><b>${esc(node.type)}</b></div>
    <div class="inspectField"><span>连接度</span><b>${node.degree}</b></div>
    <div class="inspectField"><span>说明</span><b>${esc(node.description || "-")}</b></div>
    ${links.map((edge) => {
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      const other = lookup.get(otherId);
      return `<button class="neighborButton" data-node-id="${esc(otherId)}">${esc(edge.relation)} → ${esc(other?.label || otherId)}</button>`;
    }).join("")}`;
  $$("#inspectorBody .neighborButton").forEach((button) => {
    button.onclick = () => {
      graphState.selectedId = button.dataset.nodeId;
      if (cy) { cy.elements().unselect(); cy.getElementById(graphState.selectedId).select(); }
      applyGraphDimming();
      showInspector(button.dataset.nodeId);
    };
  });
}

function zoomGraph(factor) {
  if (!cy) return;
  const next = Math.max(0.4, Math.min(2.4, cy.zoom() * factor));
  cy.zoom(next);
  cy.center();
}

function graphLayoutKey() {
  const identity = currentProjectId || currentOntology?.summary?.title || "draft";
  return `efeso_graph_layout:${identity}`;
}

function saveGraphLayout() {
  if (cy) {
    cy.nodes().forEach((node) => graphState.positions.set(node.id(), node.position()));
  }
  const positions = Object.fromEntries(graphState.positions.entries());
  localStorage.setItem(graphLayoutKey(), JSON.stringify({ positions }));
  setTask("图谱布局已保存", "布局仅保存在当前浏览器中。", 100);
}

function loadGraphLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(graphLayoutKey()) || "null");
    if (!saved?.positions || typeof saved.positions !== "object") return false;
    graphState.positions = new Map(Object.entries(saved.positions));
    return true;
  } catch {
    return false;
  }
}


async function saveProject() {
  if (modelEdited) await applyModelEditor();
  if (!currentOntology || currentValidation?.valid === false) throw new Error('请先修正并校验模型。');
  const data = await postJson("/api/projects", {
    id: currentProjectId,
    name: $("projectName").value,
    payload: {
      problem_text: $("problemText").value,
      domain: $("domainInput").value,
      objective_hint: $("objectiveHint").value,
      ontology: currentOntology,
      result: currentResult,
      validation: currentValidation,
    },
  });
  currentProjectId = data.id;
  clearDraft();
  setTask("项目版本已保存", `版本 ${data.version} · 数据保存在本机 SQLite。`, 100);
  await refreshProjects();
  await loadProject(data.id, data.version);
}

function populateProjectVersions(project) {
  currentProjectVersions = project.versions || [];
  $("projectVersion").disabled = !currentProjectVersions.length;
  $("projectVersion").innerHTML = currentProjectVersions.map((item) => `
    <option value="${item.version}" ${Number(item.version) === Number(project.version) ? "selected" : ""}>
      v${item.version} · ${esc(item.created_at.slice(0, 16).replace("T", " "))}
    </option>
  `).join("");
}

function applyProjectSnapshot(project) {
  // Expose project scenarios to renderCompare for multi-scenario mode.
  chemScenarios = (project?.payload?.scenarios || []).slice();
  const payload = project.payload || {};
  currentProjectId = project.id;
  $("projectName").value = project.name;
  $("problemText").value = payload.problem_text || "";
  $("domainInput").value = payload.domain || "";
  $("objectiveHint").value = payload.objective_hint || "";
  if (payload.ontology) applyOntology(payload.ontology);
  currentResult = payload.result || null;
  renderValidation(payload.validation || null);
  renderResults();
  updateMetrics();
  populateProjectVersions(project);
  setTask("项目已加载", `${project.name} · 版本 ${project.version}`, 100);
}

async function loadProject(projectId, version = null) {
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  const project = await getJson(`/api/projects/${projectId}${query}`);
  applyProjectSnapshot(project);
}

async function refreshProjects() {
  const data = await getJson("/api/projects");
  $("projectList").innerHTML = data.projects.length
    ? data.projects.map((project) => `<button class="projectItem" data-project-id="${project.id}">
        <strong>${esc(project.name)}</strong><span>v${project.latest_version}</span>
        <span>${esc(project.updated_at.slice(0, 16).replace("T", " "))}</span>
      </button>`).join("")
    : `<div class="validationEmpty">尚无本地项目。</div>`;
  $$("#projectList .projectItem").forEach((button) => {
    button.onclick = () => loadProject(button.dataset.projectId)
      .catch((error) => setTask("项目读取失败", error.message, 100));
  });
}

function draftPayload() {
  return {
    project_name: $("projectName").value,
    problem_text: $("problemText").value,
    domain: $("domainInput").value,
    objective_hint: $("objectiveHint").value,
    ontology: currentOntology,
    result: currentResult,
    validation: currentValidation,
    saved_at: new Date().toISOString(),
  };
}

function scheduleDraftSave() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    localStorage.setItem("efeso_solver_draft_v1", JSON.stringify(draftPayload()));
  }, 500);
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem("efeso_solver_draft_v1") || "null");
    if (!draft?.problem_text || !draft?.ontology) return false;
    $("projectName").value = draft.project_name || "未命名项目";
    $("problemText").value = draft.problem_text || "";
    $("domainInput").value = draft.domain || "";
    $("objectiveHint").value = draft.objective_hint || "";
    applyOntology(draft.ontology);
    currentResult = draft.result || null;
    renderValidation(draft.validation || null);
    renderResults();
    updateMetrics();
    setTask("已恢复本地草稿", `自动保存时间：${draft.saved_at?.slice(0, 19).replace("T", " ") || "未知"}`, 100);
    return true;
  } catch {
    return false;
  }
}

function clearDraft() {
  localStorage.removeItem("efeso_solver_draft_v1");
}

async function download(url, filename) {
  if (modelEdited) await applyModelEditor();
  if (!currentOntology || currentValidation?.valid === false) throw new Error('请先修正并校验模型。');
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ontology: currentOntology, result: currentResult }),
  });
  if (!response.ok) throw new Error("导出失败。");
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function bindEvents() {
  $$(".viewTab").forEach((button) => button.onclick = () => setView(button.dataset.view));
  if (typeof bindAtpHandlers === "function") bindAtpHandlers();
  $$(".dataTab").forEach((button) => {
    button.onclick = () => {
      $$(".dataTab").forEach((item) => item.classList.toggle("active", item === button));
      $$(".ontologyTablePane").forEach((pane) =>
        pane.classList.toggle("active", pane.dataset.tablePane === button.dataset.table));
    };
  });
  $("providerName").onchange = async () => { updateProviderUI(); await loadProviders(); renderSystemOverview(); };
  $("apiKey").oninput = () => sessionStorage.setItem("deepseek_api_key", $("apiKey").value);
  $("checkProvider").onclick = checkProvider;
  $("loadExample").onclick = () => loadExample().catch((error) => setTask("示例加载失败", error.message, 100));
  $("analyzeBtn").onclick = () => analyzeProblem().catch((error) => setTask("建模失败", error.message, 100));
  $("viewGraphFromSystem").onclick = () => setView("graphView");
  $("viewOntologyFromSystem").onclick = () => setView("ontologyView");
  $("viewModelFromSystem").onclick = () => setView("modelView");
  $("solveFromSystem").onclick = () => solveProblem().catch((error) => setTask("求解失败", error.message, 100));
  $("validateModel").onclick = () => applyModelEditor().catch((error) => setTask("模型校验失败", error.message, 100));
  $("applyModel").onclick = () => applyModelEditor().catch((error) => setTask("应用模型失败", error.message, 100));
  $("formatModel").onclick = () => {
    try { $("modelEditor").value = pretty(JSON.parse($("modelEditor").value)); }
    catch (error) { setTask("JSON 格式错误", error.message, 100); }
  };
  $("solveBtn").onclick = () => solveProblem().catch((error) => setTask("求解失败", error.message, 100));
  $("cancelTask").onclick = async () => {
    if (activeTaskId) await postJson(`/api/tasks/${activeTaskId}/cancel`, {});
  };
  $("saveProject").onclick = () => saveProject().catch((error) => setTask("保存失败", error.message, 100));
  $("projectVersion").onchange = () => {
    if (currentProjectId && $("projectVersion").value) {
      loadProject(currentProjectId, Number($("projectVersion").value))
        .catch((error) => setTask("版本读取失败", error.message, 100));
    }
  };
  $("refreshProjects").onclick = () => refreshProjects().catch((error) => setTask("项目读取失败", error.message, 100));
  $("graphSearch").oninput = () => drawGraph();
  $("relationFilter").onchange = () => { graphState.relation = $("relationFilter").value; drawGraph(true); };
  $("fitGraph").onclick = () => { if (cy) { cy.fit(undefined, 30); } };
  $("relayoutGraph").onclick = () => { graphState.positions.clear(); drawGraph(true); };
  $("graphNeighborhood").onclick = () => {
    if (!graphState.selectedId) {
      setTask("请先选择图谱节点", "选择节点后可只显示其一阶邻域。", 100);
      return;
    }
    graphState.neighborhoodOnly = !graphState.neighborhoodOnly;
    $("graphNeighborhood").classList.toggle("active", graphState.neighborhoodOnly);
    drawGraph();
  };
  $("saveGraphLayout").onclick = saveGraphLayout;
  $("graphFullscreen").onclick = () => {
    const surface = $("graphSurface");
    if (document.fullscreenElement) document.exitFullscreen();
    else surface.requestFullscreen().catch((error) => setTask("无法进入全屏", error.message, 100));
  };
  $("zoomOut").onclick = () => zoomGraph(.82);
  $("zoomIn").onclick = () => zoomGraph(1.2);
  $("setBaseline").onclick = () => {
    if (!currentResult) return setTask("没有可设为基线的结果", "请先执行本地求解。", 100);
    baselineResult = JSON.parse(JSON.stringify(currentResult));
    renderCompare();
    setTask("基线方案已设置", `目标值 ${baselineResult.objective_value}`, 100);
  };
  $("exportJson").onclick = () => download("/api/export/json", "optimization_project.json").catch((error) => setTask("导出失败", error.message, 100));
  $("exportExcel").onclick = () => download("/api/export/excel", "optimization_project.xlsx").catch((error) => setTask("导出失败", error.message, 100));
  $("exportGraphml").onclick = () => download("/api/export/graphml", "ontology.graphml").catch((error) => setTask("导出失败", error.message, 100));
  $("exportCypher").onclick = () => download("/api/export/cypher", "ontology.cypher").catch((error) => setTask("导出失败", error.message, 100));

  ["projectName", "problemText", "domainInput", "objectiveHint", "modelEditor"].forEach((id) => {
    $(id).addEventListener("input", scheduleDraftSave);
  });
  $("modelEditor").addEventListener("input", () => {
    modelEdited = true;
    modelRevision += 1;
    currentResult = null;
    currentValidation = null;
    renderResults();
    updateMetrics();
    setTask("模型已修改", "点击应用修改或计算最优方案，将使用当前编辑内容。", 0);
  });

}

async function init() {
  bindEvents();
  lockExampleControls(true);
  try {
    $("apiKey").value = sessionStorage.getItem("deepseek_api_key") || "";
    updateProviderUI();
    await loadProviders();
    if (!restoreDraft()) await loadExample();
    await refreshProjects();
  } finally {
    lockExampleControls(false);
  }
  checkProvider();
}

init().catch((error) => setTask("初始化失败", error.message, 100));





// ---------------------------------------------------------------------------
// Chemical ATP layer (MVP-1) - adapted from SAP aATP algorithm
// ---------------------------------------------------------------------------


// Invalidate results as soon as their inputs change; ignore late responses.
const businessRevision = { atp: 0, promise: 0, rank: 0, kit: 0, batch: 0 };
const businessResults = {
  atp: ['atpSummary', 'atpTable', 'atpAlertsList', 'atpRawResult', 'atpDetailContent', 'atpMaterialSelect'],
  promise: ['promiseSummary', 'promiseTable', 'promiseAlternates', 'promiseRawResult'],
  rank: ['rankSummary', 'rankTable', 'rankRawResult'],
  kit: ['kitGauge', 'kitSummary', 'kitTable', 'kitRecovery', 'kitSkippedSubs', 'kitRawResult'],
  batch: ['kitBatchResult'],
};
function businessMessage(group, message) {
  let node = $(group + 'Feedback');
  if (!node) {
    node = document.createElement('p');
    node.id = group + 'Feedback';
    node.className = 'emptyHint';
    node.setAttribute('role', 'status');
    $(businessResults[group][0]).before(node);
  }
  node.textContent = message;
  node.hidden = !message;
}
function invalidateBusiness(group, message = '输入已修改，请重新计算。') {
  businessRevision[group] += 1;
  businessResults[group].forEach(id => { if ($(id)) $(id).innerHTML = ''; });
  if (group === 'atp') {
    atpState.result = null;
    $('atpSaveSnapshot').disabled = true;
    if (atpMaterialChartInstance) atpMaterialChartInstance.clear();
  }
  if (group === 'kit') ['kitGauge', 'kitSkippedSubs'].forEach(id => { $(id).style.display = 'none'; });
  businessMessage(group, message);
  return businessRevision[group];
}
function invalidateDataset() {
  Object.keys(businessRevision).forEach(group => invalidateBusiness(group, '数据已更换，请重新计算。'));
}
function readHorizon() {
  const value = Number($('atpHorizon').value);
  if (!Number.isInteger(value) || value < 1 || value > 180) throw new Error('计算周期必须是 1 至 180 天的整数。');
  return value;
}
function applyBusinessDataset(data, projectId) {
  invalidateDataset();
  atpState.materials = data.materials || [];
  atpState.inventory = data.inventory || [];
  atpState.inbound = data.inbound || [];
  atpState.openOrders = data.open_orders || [];
  atpState.priorities = data.priorities || [];
  atpState.projectId = projectId;
  kitState.bom = data.bom || [];
  kitState.substitutes = data.substitutes || [];
  populatePromiseMaterialSelect();
  populateKitParentSelect();
  ['atpCompute', 'promiseSolve', 'rankCompute', 'kitCheck'].forEach(id => { $(id).disabled = !atpState.materials.length; });
}

const atpState = {
  materials: [],
  inventory: [],
  inbound: [],
  openOrders: [],
  priorities: [],
  horizonDays: 30,
  result: null,
};

async function jumpToGenericView(viewId) {
  if (atpState.projectId || !currentProjectId) {
    if (atpState.projectId) {
      try {
        await loadProject(atpState.projectId);
      } catch (err) {
        setTask("跳转失败", err.message, 0);
        return;
      }
    } else {
      setTask("跳转失败", "请先加载示例数据或导入业务数据", 0);
      return;
    }
  }
  setView(viewId);
}

function atpMaterialById(id) {
  return atpState.materials.find((m) => (m.id || m.material_id) === id);
}

function buildAtpMatrixTable(matrix) {
  if (!matrix.length) {
    return "<tbody><tr><td>暂无 ATP 数据，请先上传主数据并点击“计算 ATP”。</td></tr></tbody>";
  }
  const dates = matrix[0].series.map((entry) => entry.date);
  const head = ['<th>物料</th><th>分类</th><th>特控</th><th>关键</th>', ...dates.map((d) => `<th>${esc(d.slice(5))}</th>`)].join('');
  const rows = matrix.map((row) => {
    const seriesMap = Object.fromEntries(row.series.map((s) => [s.date, s]));
    const cells = dates.map((d) => {
      const entry = seriesMap[d];
      if (!entry) return '<td>-</td>';
      const cls = entry.level === 'short' ? 'atp-short' : entry.level === 'tight' ? 'atp-tight' : entry.level === 'watch' ? 'atp-watch' : 'atp-ok';
      return `<td class="${cls}" title="${esc(d)}: ${entry.atp}">${Math.round(entry.atp)}</td>`;
    }).join('');
    const sc = row.special_control || {};
    const cm = row.critical || {};
    const scLabel = sc.label || '普通';
    const cmLabel = cm.critical ? `⚠ ${cm.supply_risk}` : '—';
    return `<tr><td>${esc(row.name || row.material_id)}</td><td>${esc(row.category || '')}</td><td>${esc(scLabel)}</td><td>${esc(cmLabel)}</td>${cells}</tr>`;
  }).join('');
  return `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody>`;
}

function renderAtpResult() {
  const result = atpState.result;
  if (!result) {
    $("atpTable").innerHTML = "<tbody><tr><td>尚未计算</td></tr></tbody>";
    $("atpSummary").innerHTML = "";
    $("atpAlertsList").innerHTML = "";
    return;
  }
  $("atpTable").innerHTML = buildAtpMatrixTable(result.matrix);
  const summary = result.summary || {};
  $("atpSummary").innerHTML = `
    <div class="atpStat"><strong>${summary.materials || 0}</strong><span>物料数</span></div>
    <div class="atpStat"><strong>${(result.ots?.overall || 0).toFixed(1)}%</strong><span>整体 OTS</span></div>
    <div class="atpStat"><strong>${(result.ots?.premium || 0).toFixed(1)}%</strong><span>VIP 客户 OTS</span></div>
    <div class="atpStat"><strong>${(result.alerts || []).length}</strong><span>预警数</span></div>
    <div class="atpStat"><strong>${summary.elapsed_ms || 0} ms</strong><span>计算耗时</span></div>
  `;
  const alerts = result.alerts || [];
  $("atpAlertsList").innerHTML = alerts.length
    ? alerts.slice(0, 30).map((a) => `<div class="alertRow sev-${esc(a.severity || 'info')}"><span>${esc(a.date || '')}</span><b>${esc(a.material_id)}</b> ATP=${esc(a.atp)} <em>${esc(a.severity || '')}</em></div>`).join('')
    : '<div class="emptyHint">暂无缺口预警</div>';
  $("atpRawResult").textContent = JSON.stringify(result, null, 2);
  // Populate material select
  const select = $("atpMaterialSelect");
  select.innerHTML = result.matrix
    .filter((row) => !row.isolated && row.series && row.series.length)
    .map((row) => `<option value="${esc(row.material_id)}">${esc(row.name || row.material_id)}</option>`)
    .join('');
  if (select.value) {
    showAtpMaterialDetail(select.value);
  }
}

function showAtpMaterialDetail(materialId) {
  const row = (atpState.result?.matrix || []).find((r) => r.material_id === materialId);
  if (!row) {
    $("atpDetailContent").innerHTML = "";
    return;
  }
  const series = row.series || [];
  const minAtp = series.reduce((m, s) => Math.min(m, s.atp), Infinity);
  const maxAtp = series.reduce((m, s) => Math.max(m, s.atp), -Infinity);
  const start = series[0]?.atp ?? 0;
  const end = series[series.length - 1]?.atp ?? 0;
  $("atpDetailContent").innerHTML = `
    <div class="atpDetailGrid">
      <div><span>起始 ATP</span><strong>${Math.round(start)}</strong></div>
      <div><span>末尾 ATP</span><strong>${Math.round(end)}</strong></div>
      <div><span>最低 ATP</span><strong>${Math.round(minAtp)}</strong></div>
      <div><span>最高 ATP</span><strong>${Math.round(maxAtp)}</strong></div>
      <div><span>特控等级</span><strong>${esc(row.special_control?.label || '普通')}</strong></div>
      <div><span>关键物料</span><strong>${row.critical?.critical ? `是 (${esc(row.critical.supply_risk)})` : '否'}</strong></div>
    </div>
  `;
  renderAtpMaterialChart(row);
}

let atpMaterialChartInstance = null;
function renderAtpMaterialChart(row) {
  const dom = $("atpMaterialChart");
  if (!dom || typeof echarts === "undefined") return;
  const series = row.series || [];
  if (!series.length) {
    if (atpMaterialChartInstance) { atpMaterialChartInstance.dispose(); atpMaterialChartInstance = null; }
    dom.innerHTML = "";
    return;
  }
  const dates = series.map((s) => s.date.slice(5));
  const atpValues = series.map((s) => s.atp);
  const safety = row.safety_stock || 0;
  const shortIdxs = [];
  series.forEach((s, i) => { if (s.atp < 0) shortIdxs.push(i); });
  if (!atpMaterialChartInstance) {
    atpMaterialChartInstance = echarts.init(dom, null, { renderer: "canvas" });
    window.addEventListener("resize", () => atpMaterialChartInstance && atpMaterialChartInstance.resize());
  }
  const shortMarkArea = shortIdxs.length ? { silent: true, itemStyle: { color: "rgba(239,68,68,0.15)" }, data: shortIdxs.map((i) => [{ xAxis: i - 0.5 }, { xAxis: i + 0.5 }]) } : undefined;
  const safetyMarkLine = safety ? { silent: true, symbol: "none", data: [{ yAxis: safety, lineStyle: { color: "#f59e0b", type: "dashed" }, label: { formatter: "安全库存 " + safety } }] } : undefined;
  atpMaterialChartInstance.setOption({
    grid: { left: 50, right: 16, top: 28, bottom: 30 },
    tooltip: { trigger: "axis", valueFormatter: (v) => Math.round(v) + " kg" },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", name: "ATP (kg)", nameTextStyle: { fontSize: 10 } },
    series: [{
      name: "ATP", type: "line", data: atpValues, smooth: true,
      symbol: "circle", symbolSize: 4,
      lineStyle: { width: 2, color: "#3b82f6" }, itemStyle: { color: "#3b82f6" },
      areaStyle: { color: "rgba(59,130,246,0.12)" },
      markLine: safetyMarkLine, markArea: shortMarkArea,
    }],
  }, true);
}

async function loadAtpDemo() {
  try {
    const demo = await getJson("/api/atp/demo");
    applyBusinessDataset(demo.data, demo.project_id || 'demo');
    await postJson('/api/atp/import', { project_id: atpState.projectId, ...demo.data });
    setTask("化工 ATP", `已加载示例：物料 ${atpState.materials.length} 条，在单 ${atpState.openOrders.length} 条`, 60);
    $("atpCompute").disabled = false;
    await computeAtp();
    return true;
  } catch (err) {
    setTask("化工 ATP", `加载示例失败：${err.message}`, 0);
    return false;
  }
}

async function computeAtp() {
  const revision = invalidateBusiness('atp', '请检查输入并重新计算。');
  if (!atpState.materials.length) {
    setTask("化工 ATP", "请先加载示例或上传数据", 0);
    return;
  }
  setTask("化工 ATP", "正在计算 ATP…", 50);
  try {
    atpState.horizonDays = readHorizon();
    const result = await postJson("/api/atp/compute", {
      project_id: atpState.projectId,
      materials: atpState.materials,
      inventory: atpState.inventory,
      inbound: atpState.inbound,
      open_orders: atpState.openOrders,
      priorities: atpState.priorities,
      horizon_days: atpState.horizonDays,
    });
    if (revision !== businessRevision.atp) return;
    businessMessage('atp', '');
    atpState.result = result;
    renderAtpResult();
    $("atpSaveSnapshot").disabled = false;
    setTask("化工 ATP", `计算完成：${result.summary.materials} 个物料，${(result.alerts || []).length} 条预警`, 100);
  } catch (err) {
    if (revision !== businessRevision.atp) return;
    businessMessage('atp', err.message);
    setTask("化工 ATP", `计算失败：${err.message}`, 0);
  }
}

async function saveAtpSnapshot() {
  if (!atpState.result) return;
  try {
    const saved = await postJson("/api/atp/snapshot", {
      name: `ATP ${new Date().toLocaleString('zh-CN')}`,
      payload: atpState.result,
    });
    setTask("化工 ATP", `快照已保存：${saved.id}`, 100);
  } catch (err) {
    setTask("化工 ATP", `保存失败：${err.message}`, 0);
  }
}

async function uploadAtpFiles() {
  const files = {
    materials: $("atpFileMaterials").files?.[0],
    inventory: $("atpFileInventory").files?.[0],
    inbound: $("atpFileInbound").files?.[0],
    open_orders: $("atpFileOrders").files?.[0],
    priorities: $("atpFilePriorities").files?.[0],
    bom: $("atpFileBom")?.files?.[0],
    substitutes: $("atpFileSubs")?.files?.[0],
  };
  if (!Object.values(files).some(Boolean)) {
    setTask("化工 ATP", "请先选择至少一个文件", 0);
    return;
  }
  const form = new FormData();
  for (const [kind, file] of Object.entries(files)) {
    if (file) form.append(kind, file);
  }
  setTask("化工 ATP", "正在上传并建立化工领域本体…", 30);
  try {
    const resp = await fetch("/api/atp/upload", { method: "POST", body: form });
    const data = await parseResponse(resp);
    if (!resp.ok) throw new Error(data?.detail || data?.error || String(data));
    applyBusinessDataset(data.data, data.project_id);
    if (data.ontology_built) {
      setTask(
        "化工领域本体已建成",
        `节点 ${data.graph.nodes} / 边 ${data.graph.edges} · 下一步跳到本体图谱验证`,
        100
      );
    } else {
      setTask("本体建造部分失败", data.ontology_error || "请检查文件", 60);
    }
    $("atpCompute").disabled = false;
    // Always load the project so the 4 generic tabs see the new data.
    if (data.project_id) {
      atpState.projectId = data.project_id;
      try { await loadProject(data.project_id); } catch (e) { /* fall through */ }
    }
    // Jump to ontology graph so the user immediately sees the chemical-
    // domain model that was built from their Excel.
    setView("graphView");
    await computeAtp();
  } catch (err) {
    setTask("化工 ATP", `上传失败：${err.message}`, 0);
  }
}

function bindAtpHandlers() {
  const inputs = {
    atp: ['atpHorizon'], promise: ['promiseMaterial', 'promiseQty', 'promiseDate', 'promiseTier'],
    rank: ['rankStrategy', 'rankWeightPri', 'rankWeightLate', 'rankWeightQty', 'rankEpsLate'],
    kit: ['kitParent', 'kitQty', 'kitUseSub'], batch: ['kitBatchInput', 'kitBatchUseSub'],
  };
  for (const [group, ids] of Object.entries(inputs)) {
    ids.forEach(id => ['input', 'change'].forEach(event => $(id).addEventListener(event, () => {
      invalidateBusiness(group);
      if (group === 'atp') ['promise', 'rank'].forEach(g => invalidateBusiness(g));
    })));
  }

  $("atpLoadDemo").onclick = loadAtpDemo;
  $("atpCompute").onclick = computeAtp;
  $("atpSaveSnapshot").onclick = saveAtpSnapshot;
  $("atpUpload").onclick = uploadAtpFiles;
  $("atpMaterialSelect").onchange = (e) => showAtpMaterialDetail(e.target.value);
  // MVP-2 tabs
  $("promiseLoadDemo").onclick = loadPromiseDemo;
  $("promiseSolve").onclick = solvePromise;
  $("rankLoadDemo").onclick = loadRankDemo;
  $("rankCompute").onclick = computeRank;
  // Chem jump bar - bridge from chem tabs to generic 本体图谱/数学模型/求解结果/方案对比
  $$(".chemJumpBtn").forEach((btn) => {
    btn.onclick = () => jumpToGenericView(btn.dataset.jump);
  });

  // Kit check (齐套性)
  $("kitLoadDemo").onclick = loadKitDemo;
  $("kitCheck").onclick = runKitCheck;
  $("kitBatchRun").onclick = runKitBatch;
}

// ---------------------------------------------------------------------------
// Order promising (MVP-2)
// ---------------------------------------------------------------------------

function populatePromiseMaterialSelect() {
  const sel = $("promiseMaterial");
  if (!sel) return;
  const items = (atpState.materials || []).filter((m) => !m.special_control_level || m.special_control_level !== 0);
  sel.innerHTML = items.map((m) => `<option value="${esc(m.id || m.material_id)}">${esc(m.name || m.id)} (${esc(m.id || m.material_id)})</option>`).join("");
  if (items.length && !$("promiseDate").value) {
    const base = new Date();
    base.setDate(base.getDate() + 7);
    $("promiseDate").value = base.toISOString().slice(0, 10);
  }
}

async function loadPromiseDemo() {
  if (!atpState.materials.length) {
    if (!await loadAtpDemo()) return;
  }
  populatePromiseMaterialSelect();
  $("promiseSolve").disabled = false;
  setTask("订单承诺", "示例已加载，请填写数量和期望交期后点「运行 CP-SAT」", 60);
}

async function solvePromise() {
  const revision = invalidateBusiness('promise', '请检查输入并重新计算。');
  if (!atpState.materials.length) {
    setTask("订单承诺", "请先加载示例", 0);
    return;
  }
  const materialId = $("promiseMaterial").value;
  const qty = Number($("promiseQty").value);
  const desired = $("promiseDate").value;
  const tier = $("promiseTier").value;
  if (!materialId || !Number.isFinite(qty) || qty <= 0 || !desired) {
    setTask("订单承诺", "请选择物料、填写大于 0 的数量和期望交期", 0);
    return;
  }
  setTask("订单承诺", "CP-SAT 求解中…", 50);
  try {
    atpState.horizonDays = readHorizon();
    const result = await postJson("/api/atp/promise", {
      materials: atpState.materials,
      inventory: atpState.inventory,
      inbound: atpState.inbound,
      open_orders: atpState.openOrders,
      request: { material_id: materialId, quantity: qty, desired_date: desired, customer_tier: tier },
      horizon_days: atpState.horizonDays,
    });
    if (revision !== businessRevision.promise) return;
    businessMessage('promise', '');
    renderPromiseResult(result);
    setTask("订单承诺", result.can_promise ? `可承诺：${result.promised_date}` : "无可承诺日期，已列出替代物料", 100);
  } catch (err) {
    if (revision !== businessRevision.promise) return;
    businessMessage('promise', err.message);
    setTask("订单承诺", `求解失败：${err.message}`, 0);
  }
}

function renderPromiseResult(r) {
  const status = r.can_promise ? "✅ 可承诺" : "❌ 不可承诺";
  const cls = r.can_promise ? "atp-ok" : "atp-short";
  const altCount = (r.alternates || []).length;
  $("promiseSummary").innerHTML = `
    <div class="atpStat"><strong>${status}</strong><span>状态</span></div>
    <div class="atpStat"><strong>${r.promised_date || "—"}</strong><span>承诺交期</span></div>
    <div class="atpStat"><strong>${r.desired_date || "—"}</strong><span>期望交期</span></div>
    <div class="atpStat"><strong>${r.on_time ? "是" : "否"}</strong><span>是否准时</span></div>
    <div class="atpStat"><strong>${r.lateness_days ?? 0}</strong><span>延期 (天)</span></div>
    <div class="atpStat"><strong>${r.solve_ms ?? 0} ms</strong><span>CP-SAT 耗时</span></div>
    <div class="atpStat"><strong>${altCount}</strong><span>替代物料</span></div>
  `;
  const rows = [
    ["物料", r.material_id],
    ["数量", `${r.quantity} kg`],
    ["期望交期", r.desired_date || "—"],
    ["承诺交期", r.promised_date || "—"],
    ["准时", r.on_time ? "✅ 是" : "❌ 否"],
    ["延期天数", r.lateness_days],
    ["当前最大可承诺", `${r.qty_available ?? 0} kg`],
    ["引擎", r.engine || ""],
  ];
  $("promiseTable").innerHTML = `<thead><tr><th>字段</th><th>值</th></tr></thead><tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="${cls}">${esc(String(v))}</td></tr>`).join("")}</tbody>`;
  const alts = r.alternates || [];
  $("promiseAlternates").innerHTML = alts.length
    ? `<table class="table"><thead><tr><th>物料</th><th>名称</th><th>承诺交期</th><th>延期</th><th>准时</th></tr></thead><tbody>${alts.map((a) => `<tr><td>${esc(a.material_id)}</td><td>${esc(a.name || "")}</td><td>${esc(a.promised_date || "")}</td><td>${a.lateness_days ?? 0}</td><td>${a.on_time ? "✅" : "❌"}</td></tr>`).join("")}</tbody></table>`
    : `<div class="emptyHint">无替代物料（同分类物料无法在 horizon 内承诺）</div>`;
  $("promiseRawResult").textContent = JSON.stringify(r, null, 2);
}

// ---------------------------------------------------------------------------
// Multi-objective ranking (MVP-2)
// ---------------------------------------------------------------------------

async function loadRankDemo() {
  if (!atpState.materials.length) {
    if (!await loadAtpDemo()) return;
  }
  $("rankCompute").disabled = false;
  setTask("多目标排序", "示例已加载，请选策略后点「运行排序」", 60);
}

async function computeRank() {
  const revision = invalidateBusiness('rank', '请检查输入并重新计算。');
  if (!atpState.materials.length) {
    setTask("多目标排序", "请先加载示例", 0);
    return;
  }
  const strategy = $("rankStrategy").value;
  const weights = {
    priority: Number($("rankWeightPri").value),
    lateness: Number($("rankWeightLate").value),
    quantity: Number($("rankWeightQty").value),
  };
  const epsilon = { max_lateness_days: Number($("rankEpsLate").value) };
  setTask("多目标排序", `运行 ${strategy}…`, 50);
  try {
    atpState.horizonDays = readHorizon();
    if (Object.values(weights).some(v => !Number.isFinite(v) || v < 0 || v > 1) || !Object.values(weights).some(v => v > 0)) throw new Error('权重必须在 0 至 1 之间，且至少一项大于 0。');
    if (!Number.isInteger(epsilon.max_lateness_days) || epsilon.max_lateness_days < 0 || epsilon.max_lateness_days > 365) throw new Error('最大延期必须是 0 至 365 天的整数。');
    const result = await postJson("/api/atp/rank", {
      materials: atpState.materials,
      inventory: atpState.inventory,
      inbound: atpState.inbound,
      open_orders: atpState.openOrders,
      priorities: atpState.priorities,
      strategy: strategy,
      weights: weights,
      epsilon: epsilon,
      horizon_days: atpState.horizonDays,
    });
    if (revision !== businessRevision.rank) return;
    businessMessage('rank', '');
    renderRankResult(result);
    setTask("多目标排序", `${strategy} 完成，${(result.ranking || result.rankings || []).length} 排序`, 100);
  } catch (err) {
    if (revision !== businessRevision.rank) return;
    businessMessage('rank', err.message);
    setTask("多目标排序", `排序失败：${err.message}`, 0);
  }
}

function renderRankResult(r) {
  const rankings = r.rankings || (r.ranking ? [r.ranking] : []);
  const k = r.kpis || {};
  $("rankSummary").innerHTML = `
    <div class="atpStat"><strong>${esc(r.strategy || "")}</strong><span>策略</span></div>
    <div class="atpStat"><strong>${rankings.length}</strong><span>排序数</span></div>
    <div class="atpStat"><strong>${k.orders ?? 0}</strong><span>订单数</span></div>
    <div class="atpStat"><strong>${k.promised ?? 0}</strong><span>可承诺</span></div>
    <div class="atpStat"><strong>${(k.ots_pct ?? 0).toFixed ? k.ots_pct.toFixed(1) : k.ots_pct}%</strong><span>OTS</span></div>
    <div class="atpStat"><strong>${r.elapsed_ms ?? 0} ms</strong><span>耗时</span></div>
  `;
  if (r.strategy === "pareto" && r.pareto_front) {
    const f = r.pareto_front;
    const frontInfo = f.length ? `<div class="paretoInfo">Pareto 前沿：${f.length} 个非劣解</div>` : "";
    const blocks = rankings.map((rk, i) => buildRankTable(rk, i, f.find((x) => x.ranking_id === i) ? "Pareto 前沿" : "次优")).join("");
    $("rankTable").innerHTML = frontInfo + blocks;
  } else {
    $("rankTable").innerHTML = rankings.length ? buildRankTable(rankings[0], 0, "主排序") : "<tbody><tr><td>无数据</td></tr></tbody>";
  }
  $("rankRawResult").textContent = JSON.stringify(r, null, 2);
}

function buildRankTable(rk, idx, label) {
  const head = `<tr><th colspan="9" style="background:var(--panel-2);text-align:left;padding:6px 10px">#${idx + 1} ${esc(label)}</th></tr>
    <tr><th>序</th><th>订单</th><th>物料</th><th>客户</th><th>等级</th><th>数量</th><th>可承诺</th><th>延期</th><th>分数</th></tr>`;
  const body = rk.map((o, i) => {
    const cls = o.can_fulfill ? "atp-ok" : "atp-short";
    return `<tr class="${cls}"><td>${i + 1}</td><td>${esc(o.order_id || "")}</td><td>${esc(o.material_id || "")}</td><td>${esc(o.customer_id || "")}</td><td>${esc(o.customer_tier || "")}</td><td>${o.quantity ?? 0}</td><td>${o.promised_qty ?? 0}</td><td>${o.lateness_days ?? 0}</td><td>${o.score ?? 0}</td></tr>`;
  }).join("");
  return `<thead>${head}</thead><tbody>${body}</tbody>`;
}

// ---------------------------------------------------------------------------
// Material kit check (齐套性 MVP-3)
// ---------------------------------------------------------------------------

let kitState = { bom: [], substitutes: [] };

function populateKitParentSelect() {
  const sel = $("kitParent");
  if (!sel) return;
  const bomParents = new Set((kitState.bom || []).map((b) => b.parent_material_id));
  // Also include any material that has a parent role elsewhere (children -> parents)
  const items = (atpState.materials || []).filter((m) => bomParents.has(m.id || m.material_id) || (m.category || "").toLowerCase() === "finished");
  if (!items.length) {
    items.push(...(atpState.materials || []));
  }
  sel.innerHTML = items.map((m) => `<option value="${esc(m.id || m.material_id)}">${esc(m.name || m.id)} (${esc(m.id || m.material_id)}, ${esc(m.category || "")})</option>`).join("");
}

async function loadKitDemo() {
  if (!await loadAtpDemo()) return;
  setTask('物料齐套', `示例已加载：BOM ${kitState.bom.length} 条，替代料 ${kitState.substitutes.length} 条`, 60);
}

async function runKitCheck() {
  const revision = invalidateBusiness('kit', '请检查输入并重新计算。');
  if (!atpState.materials.length) {
    setTask("物料齐套", "请先加载示例", 0);
    return;
  }
  const parent = $("kitParent").value;
  const qty = Number($("kitQty").value);
  const useSub = $("kitUseSub").checked;
  if (!parent || !Number.isFinite(qty) || qty <= 0) {
    setTask("物料齐套", "请选择父物料并填写大于 0 的数量", 0);
    return;
  }
  setTask("物料齐套", "展开 BOM 齐套检查中…", 50);
  try {
    const result = await postJson("/api/atp/kit-check", {
      project_id: atpState.projectId,
      parent_material_id: parent,
      quantity: qty,
      materials: atpState.materials,
      inventory: atpState.inventory,
      inbound: atpState.inbound,
      bom: kitState.bom,
      substitutes: kitState.substitutes,
      use_substitutes: useSub,
    });
    if (revision !== businessRevision.kit) return;
    businessMessage('kit', '');
    renderKitResult(result);
    const s = result.summary || {};
    setTask("物料齐套", `${result.status} - 齐套 ${s.sufficient || 0}/${s.total_leaves || 0}, 缺 ${s.short || 0}, 完成度 ${s.completion_pct || 0}%`, 100);
  } catch (err) {
    if (revision !== businessRevision.kit) return;
    businessMessage('kit', err.message);
    setTask("物料齐套", `检查失败：${err.message}`, 0);
  }
}

function renderKitResult(r) {
  const s = r.summary || {};
  const statusMap = { complete: "✅ 齐套", incomplete: "❌ 缺口", recoverable: "⚠️ 可恢复", partial: "🟡 部分可恢复", error: "⛔ 错误" };
  // Completion gauge
  const pct = Number(s.completion_pct || 0);
  const ring = pct >= 100 ? "#16a34a" : pct >= 80 ? "var(--accent)" : pct >= 50 ? "#d97706" : "#dc2626";
  const shortSummary = (s.short || 0) > 0
    ? `缺料 <strong style="color:#dc2626">${s.short}</strong> 项 / 总缺口 <strong style="color:#dc2626">${s.total_gap || 0}</strong>`
    : (s.recoverable ? `可恢复 <strong style="color:#d97706">${s.recoverable}</strong> 项（恢复率 <strong>${s.recovery_rate || 0}%</strong>）` : "全部齐套，无需补料");
  const gaugeEl = $("kitGauge");
  if (gaugeEl) {
    gaugeEl.style.setProperty("--pct", String(pct));
    gaugeEl.innerHTML = `
      <div class="kitGaugeRing" style="background:conic-gradient(${ring} calc(var(--pct)*1%), var(--panel) 0)"><span>${pct}%</span></div>
      <div class="kitGaugeBody">
        <strong>${esc(statusMap[r.status] || r.status)} - ${esc(r.parent_name || r.parent || "-")} ${r.parent_quantity || 0}${esc(r.parent_unit || "kg")}</strong>
        ${shortSummary}<br/>
        <span>总需用 <code>${s.total_required || 0}</code> · 总可用 <code>${s.total_available || 0}</code> · BOM 层级 <code>${r.depth || 0}</code>${s.recovery_rate != null ? ` · 恢复后剩余缺口 <code>${s.residual_gap_after_recovery || 0}</code>` : ""}</span>
      </div>`;
    gaugeEl.style.display = "flex";
  }
  $("kitSummary").innerHTML = `
    <div class="atpStat"><strong>${esc(statusMap[r.status] || r.status)}</strong><span>整体状态</span></div>
    <div class="atpStat"><strong>${esc(r.parent_name || r.parent || "-")}</strong><span>父物料 (${esc(r.parent || "-")})</span></div>
    <div class="atpStat"><strong>${r.parent_quantity || 0}</strong><span>需求 (${esc(r.parent_unit || "kg")})</span></div>
    <div class="atpStat"><strong>${s.total_leaves || 0}</strong><span>叶子数</span></div>
    <div class="atpStat"><strong>${s.sufficient || 0}</strong><span>齐套</span></div>
    <div class="atpStat"><strong>${s.short || 0}</strong><span>缺口</span></div>
    <div class="atpStat"><strong>${s.surplus || 0}</strong><span>有富余</span></div>
    <div class="atpStat"><strong>${r.depth || 0}</strong><span>BOM 层级</span></div>
    <div class="atpStat"><strong style="color:${ring}">${pct}%</strong><span>齐套完成度</span></div>
    <div class="atpStat"><strong>${s.total_required || 0}</strong><span>总需用量</span></div>
    <div class="atpStat"><strong>${s.total_available || 0}</strong><span>总可用</span></div>
    <div class="atpStat"><strong style="color:${s.total_gap > 0 ? "#dc2626" : "var(--ok, #16a34a)"}">${s.total_gap || 0}</strong><span>总缺口</span></div>
    ${s.recoverable != null ? `<div class="atpStat"><strong>${s.recoverable}/${s.short || 0}</strong><span>可恢复叶子</span></div>` : ""}
    ${s.recovery_rate != null ? `<div class="atpStat"><strong>${s.recovery_rate}%</strong><span>恢复率</span></div>` : ""}
    ${s.residual_gap_after_recovery != null ? `<div class="atpStat"><strong>${s.residual_gap_after_recovery}</strong><span>恢复后剩余</span></div>` : ""}
  `;
  const head = `<tr><th>物料</th><th>名称</th><th>分类</th><th>需用</th><th>在手</th><th>在途</th><th>可用</th><th>缺口</th><th>状态</th><th>特控</th><th>原因</th></tr>`;
  const body = (r.leaves || []).map((l) => {
    const cls = l.status === "short" ? "atp-short" : l.isolated ? "atp-watch" : "atp-ok";
    return `<tr class="${cls}">
      <td>${esc(l.material_id)}</td>
      <td>${esc(l.name || "")}</td>
      <td>${esc(l.category || "")}</td>
      <td>${(l.required_qty || 0).toFixed(1)}</td>
      <td>${(l.on_hand || 0).toFixed(1)}</td>
      <td>${(l.in_transit || 0).toFixed(1)}</td>
      <td>${(l.available || 0).toFixed(1)}</td>
      <td>${(l.short_qty || 0).toFixed(1)}</td>
      <td>${l.status === "short" ? "缺口" : l.isolated ? "隔离" : "齐套"}</td>
      <td>${l.special_control != null ? `L${l.special_control}` : "-"}${l.isolated ? " 隔离" : ""}</td>
      <td><span class="kitTableReason ${l.reason && l.reason.code === "isolated" ? "kitReasonIsolated" : l.reason && l.reason.code === "buffer_deducted" ? "kitReasonBuffer" : l.reason && l.reason.code === "ok" ? "kitTableReasonOk" : "kitReasonShort"}">${l.reason ? esc(l.reason.message) : "-"}</span></td>
    </tr>`;
  }).join("");
  $("kitTable").innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  // Recovery
  const rec = r.recovery || [];
  if (rec.length) {
    $("kitRecovery").innerHTML = `<table class="table"><thead><tr><th>缺口物料</th><th>缺口量</th><th>替代料</th><th>可用量</th><th>可补</th><th>剩余缺口</th><th>完全可恢复</th></tr></thead><tbody>${
      rec.map((rec) => {
        const cells = (rec.candidates || []).slice(0, 3).map((c) => `${esc(c.substitute_id)} (${(c.available || 0).toFixed(1)}) → 补 ${(c.can_cover || 0).toFixed(1)}`).join("<br/>");
        return `<tr><td>${esc(rec.material_id)} ${esc(rec.name || "")}</td><td>${(rec.short_qty || 0).toFixed(1)}</td><td>${cells || "-"}</td><td>-</td><td>-</td><td>${(rec.residual_gap || 0).toFixed(1)}</td><td>${rec.fully_recoverable ? "✅" : "❌"}</td></tr>`;
      }).join("")
    }</tbody></table>`;
  } else {
    $("kitRecovery").innerHTML = `<div class="emptyHint">${r.status === "complete" ? "无缺口，无需替代料" : "无可用替代料（建议补料或调整 BOM）"}</div>`;
  }
  // Skipped isolated substitutes
  const skipped = r.skipped_isolated_substitutes || [];
  if (skipped.length) {
    const html = `<strong>⚠ 跳过的隔离替代料（${skipped.length}）</strong><br/>` + skipped.map((s) => `• <code>${esc(s.original)}</code> → <code>${esc(s.substitute)}</code>（${esc(s.reason)}）`).join("<br/>");
    $("kitSkippedSubs").style.display = "block";
    $("kitSkippedSubs").innerHTML = html;
  } else {
    $("kitSkippedSubs").style.display = "none";
    $("kitSkippedSubs").innerHTML = "";
  }
  $("kitRawResult").textContent = JSON.stringify(r, null, 2);
}

async function runKitBatch() {
  const revision = invalidateBusiness('batch', '请检查输入并重新计算。');
  const text = ($("kitBatchInput").value || "").trim();
  if (!text) {
    setTask("物料齐套", "请输入批量检查的物料清单", 0);
    return;
  }
  const items = [];
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    const segs = line.trim().split(/[,，\s]+/);
    const qty = Number(segs[1]);
    if (segs.length !== 2 || !Number.isFinite(qty) || qty <= 0) {
      const message = `第 ${index + 1} 行格式错误：请填写“父物料,大于 0 的数量”；本次未提交任何条目。`;
      businessMessage('batch', message);
      setTask('物料齐套', message, 0);
      return;
    }
    items.push({ parent_material_id: segs[0], quantity: qty });
  }
  const useSub = $("kitBatchUseSub").checked;
  setTask("物料齐套", `批量齐套 ${items.length} 个产品…`, 50);
  try {
    const result = await postJson("/api/atp/kit-check/batch", {
      project_id: atpState.projectId,
      items,
      materials: atpState.materials,
      inventory: atpState.inventory,
      inbound: atpState.inbound,
      bom: kitState.bom,
      substitutes: kitState.substitutes,
      use_substitutes: useSub,
    });
    if (revision !== businessRevision.batch) return;
    businessMessage('batch', '');
    renderKitBatchResult(result);
    const sm = result.summary || {};
    setTask("物料齐套", `批量完成：齐套 ${sm.complete_items || 0} / 缺 ${sm.short_items || 0} / 总缺口 ${sm.total_gap || 0}`, 100);
  } catch (err) {
    if (revision !== businessRevision.batch) return;
    businessMessage('batch', err.message);
    setTask("物料齐套", `批量失败：${err.message}`, 0);
  }
}

function renderKitBatchResult(r) {
  const sm = r.summary || {};
  const items = r.items || [];
  const head = `<tr><th>父物料</th><th>名称</th><th>状态</th><th>齐套完成度</th><th>总需用</th><th>总缺口</th><th>可恢复</th></tr>`;
  const body = items.map((it) => {
    const s2 = it.summary || {};
    const statusMap = { complete: "✅", incomplete: "❌", recoverable: "⚠️", partial: "🟡", error: "⛔" };
    const color = (s2.completion_pct || 0) >= 100 ? "#16a34a" : (s2.completion_pct || 0) >= 80 ? "var(--accent)" : (s2.completion_pct || 0) >= 50 ? "#d97706" : "#dc2626";
    return `<tr>
      <td><code>${esc(it.parent_material_id)}</code></td>
      <td>${esc(it.parent_name || "")}</td>
      <td>${statusMap[it.status] || it.status || "-"}</td>
      <td style="color:${color};font-weight:600">${s2.completion_pct || 0}%</td>
      <td>${s2.total_required || 0}</td>
      <td style="color:${s2.total_gap > 0 ? "#dc2626" : "var(--ok, #16a34a)"}">${s2.total_gap || 0}</td>
      <td>${s2.recoverable != null ? `${s2.recoverable}/${s2.short}` : "-"}</td>
    </tr>`;
  }).join("");
  const problems = (r.problem_materials || []).map((p) => `<code>${esc(p.material_id)}</code> 缺 <strong>${p.total_short_qty}</strong>`).join("、 ") || "无";
  $("kitBatchResult").innerHTML = `
    <div class="atpSummary" style="grid-template-columns:repeat(5,1fr)">
      <div class="atpStat"><strong>${sm.total_items || 0}</strong><span>产品数</span></div>
      <div class="atpStat"><strong style="color:#16a34a">${sm.complete_items || 0}</strong><span>齐套</span></div>
      <div class="atpStat"><strong style="color:#d97706">${sm.recoverable_items || 0}</strong><span>可恢复</span></div>
      <div class="atpStat"><strong style="color:#dc2626">${sm.short_items || 0}</strong><span>有缺口</span></div>
      <div class="atpStat"><strong>${sm.completion_pct || 0}%</strong><span>总完成度</span></div>
    </div>
    <div style="margin:8px 0;font-size:12px"><strong>缺料清单：</strong>${problems}</div>
    <table class="kitBatchTable"><thead>${head}</thead><tbody>${body}</tbody></table>
  `;
}