/**
 * static/ui.js
 * ============
 * UI 操作函数：状态面板切换、进度条、日志、提取数据表格渲染。
 *
 * 公开函数：
 *   showState(panelEl)                          → 切换三个状态面板
 *   setProgress(percent, text, hint)            → 更新进度条
 *   addLog(tag, msg, type)                      → 追加日志行
 *   clearLogs()                                 → 清空日志
 *   renderExtractedTable(fields, outputFilename) → 渲染提取数据表格
 *
 * 依赖（全局 var）：
 *   i18n.js    → t()
 *   utils.js   → escapeHtml(), getTimeStr()
 *   app.js     → progressBar, progressText, progressPercent,
 *                progressHint, logsBody, extractedFields
 */

// ── 状态面板 ──────────────────────────────────────────────────────────────────

function showState(state) {
  [stateUpload, stateProcessing, stateComplete].forEach(function(el) {
    el.classList.remove("active");
  });
  state.classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── 进度条 ────────────────────────────────────────────────────────────────────

function setProgress(percent, text, hint) {
  progressBar.style.width = percent + "%";
  progressPercent.textContent = percent + "%";
  if (text) progressText.textContent = text;
  if (hint) progressHint.textContent = hint;
}

// ── 日志 ──────────────────────────────────────────────────────────────────────

function clearLogs() {
  logsBody.innerHTML = "";
}

/**
 * 向日志面板追加一行。
 * @param {string} tag   - 标签文字，如 "INFO" / "SUCCESS" / "ERROR"
 * @param {string} msg   - 日志内容
 * @param {string} type  - 样式类型：info | success | warn
 */
function addLog(tag, msg, type) {
  type = type || "info";
  var empty = logsBody.querySelector(".log-empty");
  if (empty) empty.remove();

  var line = document.createElement("div");
  line.className = "log-line";
  line.innerHTML =
    '<span class="log-time">' + getTimeStr() + '</span>' +
    '<span class="log-tag log-tag-' + type + '">[' + tag + ']</span>' +
    '<span class="log-msg">' + msg + '</span>';
  logsBody.appendChild(line);
  logsBody.scrollTop = logsBody.scrollHeight;
}

// ── 提取数据表格 ──────────────────────────────────────────────────────────────

/**
 * 渲染提取结果表格，并绑定手动编辑输入框的 input 事件。
 * @param {Array}  fields         - 提取字段列表
 * @param {string} outputFilename - 输出文件名（传给 regenerateExcel）
 */
function renderExtractedTable(fields, outputFilename) {
  var container = document.getElementById("extracted-table-container");
  if (!container || !fields.length) return;

  var filledCount    = fields.filter(function(f) { return f.filled; }).length;
  var totalCount     = fields.length;
  var emptyCount     = totalCount - filledCount;
  var inferredCount  = fields.filter(function(f) { return f.note; }).length;
  var accuracy       = totalCount > 0 ? Math.round(filledCount / totalCount * 100) : 0;
  var hasSource      = fields.some(function(f) { return f.source; });
  var hasNotes       = fields.some(function(f) { return f.note; });

  // 提示消息
  var hintHtml = "";
  if (accuracy < 70) {
    hintHtml = '<div class="manual-hint manual-hint-warn">⚠ ' + t("manual_hint_low") + "</div>";
  } else if (inferredCount > 0) {
    hintHtml = '<div class="manual-hint manual-hint-info">✎ ' + t("manual_hint_note") + "</div>";
  } else if (emptyCount > 0) {
    hintHtml = '<div class="manual-hint manual-hint-info">✎ ' + t("manual_hint_empty") + "</div>";
  }

  var rows = fields.map(function(f, i) {
    var statusClass   = f.filled ? "status-filled" : "status-empty";
    var statusText    = f.filled ? t("status_filled") : t("status_empty");
    var noteHtml      = f.note
      ? '<span class="note-badge">⚠ ' + t("note_inferred") + '</span>' +
        '<span class="note-text">' + escapeHtml(f.note) + "</span>"
      : "";
    var inputClass    = f.filled ? "field-edit-input" : "field-edit-input field-edit-empty";
    var inputPlaceholder = f.filled ? "" : t("edit_placeholder");

    return (
      '<tr class="' + (f.filled ? "" : "row-empty") + (f.note ? " row-inferred" : "") +
      '" data-field-id="' + f.id + '">' +
      "<td>" + (i + 1) + "</td>" +
      '<td class="field-label">' + escapeHtml(f.label) + "</td>" +
      '<td class="field-cell">' + f.cell + "</td>" +
      '<td class="field-value-cell">' +
        '<input type="text" class="' + inputClass + '" data-id="' + f.id + '"' +
        ' value="' + escapeHtml(f.value || "") + '"' +
        ' placeholder="' + inputPlaceholder + '">' +
      "</td>" +
      (hasSource ? '<td class="field-source">' + escapeHtml(f.source || "-") + "</td>" : "") +
      '<td class="field-status"><span class="status-badge-sm ' + statusClass + '" data-status="' + f.id + '">' + statusText + "</span></td>" +
      '<td class="field-preserve">' +
        '<input type="checkbox" class="field-preserve-checkbox" data-preserve-id="' + f.id + '"' +
        (f.preserveOnReupload ? " checked" : "") +
        ' aria-label="' + escapeHtml(t("preserve_field")) + '">' +
      "</td>" +
      (hasNotes ? '<td class="field-note">' + noteHtml + "</td>" : "") +
      "</tr>"
    );
  }).join("");

  var html =
    '<div class="extracted-table-header">' +
      '<h3>' + t("extracted_title") + "</h3>" +
      '<span class="extracted-summary">' + filledCount + "/" + totalCount + "</span>" +
    "</div>" +
    hintHtml +
    '<div class="extracted-table-wrap">' +
      '<table class="extracted-table" id="editable-fields-table">' +
        "<thead><tr>" +
          "<th>#</th>" +
          "<th>" + t("col_field") + "</th>" +
          "<th>" + t("col_cell") + "</th>" +
          "<th>" + t("col_value") + "</th>" +
          (hasSource ? "<th>" + t("col_source") + "</th>" : "") +
          "<th>" + t("col_status") + "</th>" +
          '<th class="field-preserve">' + t("col_preserve") + "</th>" +
          (hasNotes ? "<th>" + t("col_note") + "</th>" : "") +
        "</tr></thead>" +
        "<tbody>" + rows + "</tbody>" +
      "</table>" +
    "</div>" +
    '<div class="regenerate-bar">' +
      '<button class="btn-regenerate" id="regenerate-btn" onclick="regenerateExcel(\'' +
        escapeHtml(outputFilename || downloadFilename) + '\')">' +
        "↻ " + t("regenerate_btn") +
      "</button>" +
    "</div>" +
    '<div class="replacement-upload-bar">' +
      '<div class="replacement-upload-copy">' +
        '<strong>' + t("replacement_upload_title") + "</strong>" +
        '<span>' + t("replacement_upload_hint") + "</span>" +
      "</div>" +
      '<input type="file" id="replacement-file-input" accept="application/pdf,image/*" multiple hidden>' +
      '<button class="btn-replacement-upload" type="button" onclick="triggerReplacementUpload()">' +
        "↻ " + t("replacement_upload_btn") +
      "</button>" +
    "</div>";

  container.innerHTML = html;
  container.style.display = "block";

  // 绑定输入框：同步修改到 extractedFields 并更新状态徽章
  container.querySelectorAll(".field-edit-input").forEach(function(input) {
    input.addEventListener("input", function() {
      var id = input.dataset.id;
      var field = extractedFields.find(function(f) { return f.id === id; });
      if (!field) return;

      field.value = input.value;
      var badge = container.querySelector('[data-status="' + id + '"]');
      if (!badge) return;

      if (input.value.trim()) {
        badge.className = "status-badge-sm status-filled";
        badge.textContent = t("status_filled");
        input.classList.remove("field-edit-empty");
      } else {
        badge.className = "status-badge-sm status-empty";
        badge.textContent = t("status_empty");
        input.classList.add("field-edit-empty");
      }
    });
  });

  // 再アップロード時に保持する行を extractedFields に同期
  container.querySelectorAll(".field-preserve-checkbox").forEach(function(checkbox) {
    checkbox.addEventListener("change", function() {
      var field = extractedFields.find(function(f) { return f.id === checkbox.dataset.preserveId; });
      if (field) field.preserveOnReupload = checkbox.checked;
    });
  });

  var replacementInput = document.getElementById("replacement-file-input");
  if (replacementInput) {
    replacementInput.addEventListener("change", function() {
      handleReplacementFiles(replacementInput.files);
    });
  }
}

// ── nav-avatar クリック → マイページ遷移 ─────────────────────────────────────

function initProfileModal() {
  // モーダル廃止: nav-avatar クリックで /me ページに遷移する
  var avatar = document.getElementById("nav-avatar");
  if (!avatar) return;
  avatar.addEventListener("click", function () {
    window.location.href = "/me";
  });
}
