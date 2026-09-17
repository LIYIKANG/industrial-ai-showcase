/**
 * static/app.js
 * =============
 * 应用入口：共享状态变量、DOM 元素引用、事件绑定、初始化。
 *
 * 必须最后加载（依赖所有其他 JS 文件中定义的函数）。
 *
 * 加载顺序：
 *   i18n.js → utils.js → recent.js → ui.js → file-handler.js → api.js → app.js
 */

// ── 共享状态（var 确保跨文件可访问） ─────────────────────────────────────────

var currentFiles    = [];            // 当前已选文件列表
var downloadUrl     = null;          // 最近生成的下载 URL
var downloadFilename = "filled.xlsx"; // 下载文件名
var startTime       = null;          // 处理开始时间戳（用于计算耗时）
var extractedFields = [];            // 提取结果（用于手动编辑）
var replacementUploadActive = false; // 再上传覆盖模式：新结果返回后按 checkbox 合并
var replacementPreservedFields = {}; // 再上传时需要保留的字段快照（field_id → field）

// ── DOM 元素引用 ──────────────────────────────────────────────────────────────
// 二段アップロード：契約書 / 精算書のドロップゾーンは uploadSlots（file-handler.js）で管理。

var uploadBtn       = document.getElementById("upload-btn");
var downloadBtn     = document.getElementById("download-btn");
var newFileBtn      = document.getElementById("new-file-btn");
var langToggle      = document.getElementById("lang-toggle");

// 状态面板
var stateUpload     = document.getElementById("state-upload");
var stateProcessing = document.getElementById("state-processing");
var stateComplete   = document.getElementById("state-complete");

// 进度 & 日志
var progressBar     = document.getElementById("progress-bar");
var progressText    = document.getElementById("progress-text");
var progressPercent = document.getElementById("progress-percent");
var progressHint    = document.getElementById("progress-hint");
var logsBody        = document.getElementById("logs-body");

// ── 事件绑定 ──────────────────────────────────────────────────────────────────

function initEvents() {
  // 语言切换
  langToggle.addEventListener("click", switchLang);

  // 二段アップロード：各槽位（契約書 / 精算書）にファイル選択・拖拽を個別バインド
  Object.keys(uploadSlots).forEach(function(slotKey) {
    var slot = uploadSlots[slotKey];
    var dropArea = document.getElementById(slot.dropId);
    var fileInput = document.getElementById(slot.inputId);
    var browseBtn = document.getElementById(slot.browseId);
    if (!dropArea || !fileInput) return;

    dropArea.addEventListener("click", function() { fileInput.click(); });
    if (browseBtn) {
      browseBtn.addEventListener("click", function(e) {
        e.stopPropagation();
        fileInput.click();
      });
    }
    fileInput.addEventListener("change", function(e) {
      if (e.target.files.length) handleFilesForSlot(slotKey, e.target.files);
    });

    ["dragenter", "dragover", "dragleave", "drop"].forEach(function(evt) {
      dropArea.addEventListener(evt, preventDefaults, false);
    });
    ["dragenter", "dragover"].forEach(function(evt) {
      dropArea.addEventListener(evt, function() { dropArea.classList.add("highlight"); });
    });
    ["dragleave", "drop"].forEach(function(evt) {
      dropArea.addEventListener(evt, function() { dropArea.classList.remove("highlight"); });
    });
    dropArea.addEventListener("drop", function(e) {
      if (e.dataTransfer.files.length) handleFilesForSlot(slotKey, e.dataTransfer.files);
    });
  });

  // 按钮
  uploadBtn.addEventListener("click", handleUpload);
  downloadBtn.addEventListener("click", handleDownload);
  newFileBtn.addEventListener("click", handleNewFile);
}

// ── 初始化 ────────────────────────────────────────────────────────────────────

applyI18n();
initEvents();
initProfileModal();
