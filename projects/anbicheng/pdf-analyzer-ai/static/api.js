/**
 * static/api.js
 * =============
 * 与后端 API 交互：上传处理、下载、重新生成 Excel。
 *
 * 公开函数：
 *   handleUpload()             → 上传文件、调用 AI、展示结果
 *   handleDownload()           → 触发文件下载
 *   handleNewFile()            → 重置为上传初始状态
 *   regenerateExcel(filename)  → 用手动修正值重新生成 Excel
 *
 * 依赖（全局 var）：
 *   i18n.js        → t(), currentLang
 *   utils.js       → formatFileSize(), getNowStr(), sleep()
 *   ui.js          → showState(), setProgress(), addLog(), clearLogs(), renderExtractedTable()
 *   file-handler.js → resetFileUI()
 *   recent.js      → saveRecentFile()
 *   app.js         → currentFiles, downloadUrl, downloadFilename,
 *                    startTime, extractedFields, progressBar,
 *                    stateProcessing, stateComplete, stateUpload
 */

// ── API エンドポイント設定（ページごとに上書き可能） ─────────────────────────
// デフォルトは重要事項説明書（/api/process）。
// 精算書ページでは window.API_PREFIX = "/api/seisansho" を設定する。
var API_PROCESS_URL    = (window.API_PREFIX || "/api") + "/process";
var API_DOWNLOAD_URL   = (window.API_PREFIX || "/api") + "/download/";
var API_REGENERATE_URL = (window.API_PREFIX || "/api") + "/regenerate";
var API_JOBS_URL       = (window.API_PREFIX || "/api") + "/jobs/";

// ── 上传 & 处理 ───────────────────────────────────────────────────────────────

async function handleUpload() {
  if (!currentFiles.length) return;

  startTime = Date.now();
  showState(stateProcessing);

  var totalSize = currentFiles.reduce(function(s, f) { return s + f.size; }, 0);
  var displayName = currentFiles.length === 1
    ? currentFiles[0].name
    : currentFiles.length + t("files_unit");

  document.getElementById("proc-name").textContent = displayName;
  document.getElementById("stat-size").textContent = formatFileSize(totalSize);
  document.getElementById("stat-pages").textContent = "--";
  document.getElementById("stat-fields").textContent = "--";

  clearLogs();
  setProgress(5, t("uploading"), t("remaining_calc"));
  addLog("SUCCESS",
    t("log_upload_start") + " (" + currentFiles.length + t("items_unit") + ")",
    "success"
  );

  await sleep(300);
  setProgress(15, t("parsing_pdf"), t("remaining_calc"));
  addLog("INFO", t("log_parsing_meta"), "info");

  await sleep(500);
  setProgress(25, t("extracting_text"), t("remaining_about") + "15" + t("remaining_seconds"));
  addLog("INFO", t("log_checking_ocr"), "info");

  var formData = new FormData();
  currentFiles.forEach(function(f) { formData.append("pdf_files", f); });
  // 槽位情報（契約書のみモード判定用）。currentFiles と整列。
  if (!replacementUploadActive && typeof getFileSlots === "function") {
    formData.append("file_slots", JSON.stringify(getFileSlots()));
  }

  try {
    setProgress(35, t("ai_recognizing"), t("remaining_about") + "12" + t("remaining_seconds"));
    addLog("INFO", t("log_calling_ai"), "info");

    // 時間経過に応じた処理ログ（key で保持し、出力時に t() で訳す）
    var aiLogs = [
      { sec: 2,  key: "ai_log_convert_image",  type: "info" },
      { sec: 4,  key: "ai_log_send_model",     type: "info" },
      { sec: 7,  key: "ai_log_analyze_layout", type: "info" },
      { sec: 11, key: "ai_log_extract_fields", type: "info" },
      { sec: 16, key: "ai_log_validate",       type: "info" },
      { sec: 21, key: "ai_log_prepare_excel",  type: "info" },
    ];
    var aiLogsFired = [];

    // 伪进度：AI 処理中は進捗＋経過時間をリアルタイム更新
    var progressInterval = setInterval(function() {
      var cur = parseInt(progressBar.style.width);
      var elapsed = Math.round((Date.now() - startTime) / 1000);
      if (cur < 85) {
        var next = cur + Math.random() * 5 + 1;
        setProgress(
          Math.min(85, Math.round(next)),
          t("ai_analyzing"),
          t("remaining_about") + Math.max(3, 15 - elapsed) + t("remaining_seconds")
        );
      }
      // 経過時間をリアルタイム表示
      var elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
      document.getElementById("result-time") && (document.getElementById("result-time").textContent = elapsedSec + "s");
      // 時間経過ログを順次出力
      aiLogs.forEach(function(entry) {
        if (elapsed >= entry.sec && aiLogsFired.indexOf(entry.sec) === -1) {
          aiLogsFired.push(entry.sec);
          addLog("INFO", t(entry.key), entry.type);
        }
      });
    }, 1000);

    var token = sessionStorage.getItem('access_token');
    var resp = await fetch(API_PROCESS_URL, {
      method: "POST",
      body: formData,
      headers: token ? { 'Authorization': 'Bearer ' + token } : {}
    });
    clearInterval(progressInterval);

    if (resp.status === 202) {
      var data = await resp.json();
      addLog("INFO", t("job_received") + data.job_id + ")", "info");
      await _pollJobStatus(data.job_id, data.file_source || "unknown");
      return;
    }

    var contentType = resp.headers.get("Content-Type") || "";

    if (!resp.ok) {
      setProgress(100, t("process_failed"), "");
      addLog("ERROR", t("process_error"), "warn");
      if (contentType.includes("application/json")) {
        var err = await resp.json();
        var errMsg = err.error ||
          (Array.isArray(err.detail)
            ? err.detail.map(function(d) { return d.msg || JSON.stringify(d); }).join("; ")
            : err.detail) ||
          resp.statusText;
        addLog("ERROR", errMsg, "warn");
      } else {
        addLog("ERROR", resp.status + " " + resp.statusText, "warn");
      }
      progressBar.style.background = "#EF4444";
      return;
    }

    setProgress(90, t("generating_excel"), t("almost_done"));
    addLog("SUCCESS", t("ai_complete_filling"), "success");

    if (contentType.includes("application/json")) {
      var result = await resp.json();
      _applyResult(result);
    } else {
      var text = await resp.text();
      addLog("ERROR", t("unknown_response") + ": " + text, "warn");
    }

  } catch (err) {
    console.error(err);
    setProgress(100, t("request_failed"), "");
    progressBar.style.background = "#EF4444";
    addLog("ERROR", t("network_error"), "warn");
  }
}

// ── ジョブポーリング ───────────────────────────────────────────────────────────

function _pollJobStatus(jobId, fileSource) {
  return new Promise(function(resolve) {
    var token = sessionStorage.getItem('access_token');
    var pollStart = Date.now();
    var MAX_POLL_MS = 600000; // 10分

    function poll() {
      if (Date.now() - pollStart > MAX_POLL_MS) {
        setProgress(100, t("progress_timeout"), "");
        addLog("ERROR", t("progress_timeout_log"), "warn");
        progressBar.style.background = "#EF4444";
        resolve();
        return;
      }

      var elapsed = Math.round((Date.now() - startTime) / 1000);
      setProgress(
        Math.min(85, 35 + Math.floor(elapsed / 6)),
        t("job_ai_analyzing"),
        elapsed + t("seconds_elapsed_suffix")
      );
      document.getElementById("result-time") &&
        (document.getElementById("result-time").textContent = elapsed + "s");

      fetch(API_JOBS_URL + jobId, {
        headers: token ? { 'Authorization': 'Bearer ' + token } : {}
      })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(job) {
          if (!job) { setTimeout(poll, 5000); return; }

          if (job.status === "done") {
            setProgress(90, t("generating_excel"), t("almost_done"));
            addLog("SUCCESS", t("ai_complete_filling"), "success");
            job.file_source = fileSource || "unknown";
            _applyResult(job).then(function() {
              _showToast(t("toast_complete") + (job.filename || t("excel_generated")));
              resolve();
            });
          } else if (job.status === "failed") {
            setProgress(100, t("process_failed"), "");
            addLog("ERROR", job.error || t("job_failed"), "warn");
            progressBar.style.background = "#EF4444";
            resolve();
          } else {
            setTimeout(poll, 5000);
          }
        })
        .catch(function() { setTimeout(poll, 5000); });
    }

    setTimeout(poll, 5000);
  });
}

// ── トースト通知 ──────────────────────────────────────────────────────────────

var _toastContainer = null;

function _getToastContainer() {
  if (!_toastContainer || !document.body.contains(_toastContainer)) {
    _toastContainer = document.createElement("div");
    _toastContainer.style.cssText = [
      "position:fixed", "top:20px", "right:20px",
      "z-index:9999",
      "display:flex", "flex-direction:column", "gap:8px",
      "max-width:320px", "pointer-events:none"
    ].join(";");
    document.body.appendChild(_toastContainer);
  }
  return _toastContainer;
}

function _showToast(msg) {
  var container = _getToastContainer();

  var el = document.createElement("div");
  el.style.cssText = [
    "background:#10B981", "color:#fff",
    "padding:12px 16px", "border-radius:8px",
    "font-weight:600",
    "box-shadow:0 4px 12px rgba(0,0,0,.2)",
    "display:flex", "align-items:center", "gap:12px",
    "pointer-events:auto",
    "transition:opacity .3s"
  ].join(";");

  var text = document.createElement("span");
  text.textContent = msg;
  text.style.flex = "1";

  var btn = document.createElement("button");
  btn.textContent = "×";
  btn.style.cssText = [
    "background:none", "border:none", "color:#fff",
    "font-size:18px", "line-height:1",
    "cursor:pointer", "padding:0", "font-weight:bold",
    "flex-shrink:0"
  ].join(";");
  btn.onclick = function() {
    el.style.opacity = "0";
    setTimeout(function() { el.remove(); }, 300);
  };

  el.appendChild(text);
  el.appendChild(btn);
  container.appendChild(el);
}

/** 将 /api/process 的响应结果应用到 UI */
async function _applyResult(result) {
  var fieldsFilled = String(result.fields_filled || 0);
  var fieldsTotal  = String(result.fields_total  || 0);
  var pageCount    = String(result.page_count    || "--");

  document.getElementById("stat-pages").textContent  = pageCount;
  document.getElementById("stat-fields").textContent = fieldsFilled + "/" + fieldsTotal;

  downloadUrl      = API_DOWNLOAD_URL + result.file_id + "?filename=" + encodeURIComponent(result.filename);
  downloadFilename = result.filename || "filled.xlsx";

  // ファイルごとの処理結果ログ（複数ファイルの場合）
  if (result.file_results && result.file_results.length > 1) {
    result.file_results.forEach(function(fr) {
      addLog("INFO", fr.filename + " — " + fr.pages + t("pages_unit") + " / " + fr.fields_filled + t("fields_input_unit"), "info");
    });
  }

  await sleep(300);
  setProgress(100, t("process_complete"), "");
  addLog("SUCCESS",
    t("excel_success") + " " + fieldsFilled + "/" + fieldsTotal + " " + t("fields_unit") +
    "　(" + pageCount + t("pages_unit") + " / " + t("fill_rate_label") + (Number(fieldsTotal) > 0 ? Math.round(Number(fieldsFilled) / Number(fieldsTotal) * 100) : 0) + "%)",
    "success"
  );
  await sleep(600);

  // 结果页统计
  var accuracyVal = Number(fieldsTotal) > 0
    ? Math.round((Number(fieldsFilled) / Number(fieldsTotal)) * 1000) / 10 : 0;
  document.getElementById("result-accuracy").textContent = accuracyVal + "%";

  var resultFilename = currentFiles.length === 1
    ? currentFiles[0].name
    : currentFiles.map(function(f) { return f.name; }).join(", ");
  var resultFilesize = formatFileSize(currentFiles.reduce(function(s, f) { return s + f.size; }, 0));
  var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

  document.getElementById("result-fields").textContent   = fieldsFilled + "/" + fieldsTotal;
  document.getElementById("result-time").textContent     = elapsed + "s";
  document.getElementById("result-filename").textContent = resultFilename;
  document.getElementById("result-filesize").textContent = resultFilesize;
  document.getElementById("result-date").textContent     = getNowStr();
  var fileSource = String(result.file_source || "unknown").toLowerCase();
  if (["native", "scan", "photo", "unknown"].indexOf(fileSource) === -1) {
    fileSource = "unknown";
  }
  var fileSourceElement = document.getElementById("result-file-source");
  if (fileSourceElement) {
    fileSourceElement.textContent = fileSource.toUpperCase();
    fileSourceElement.setAttribute("data-source", fileSource);
  }

  // 抽出結果を手動編集用として保存する。再アップロード時は、チェック済みフィールドを既存の値で上書きする。
  var nextFields = (result.extracted || []).map(function(f) { return Object.assign({}, f); });
  if (replacementUploadActive) {
    var mergedIds = {};
    nextFields = nextFields.map(function(f) {
      mergedIds[f.id] = true;
      var preserved = replacementPreservedFields[f.id];
      return preserved
        ? Object.assign({}, f, preserved, { preserveOnReupload: true })
        : f;
    });
    Object.keys(replacementPreservedFields).forEach(function(id) {
      if (!mergedIds[id]) {
        nextFields.push(Object.assign({}, replacementPreservedFields[id], { preserveOnReupload: true }));
      }
    });
    if (!await refreshReplacementDownload(nextFields, result.filename)) {
      downloadUrl = null;
      alert(t("replacement_regenerate_failed"));
    }
    replacementUploadActive = false;
    replacementPreservedFields = {};
  }
  extractedFields = nextFields;
  renderExtractedTable(extractedFields, result.filename);
  var mergedFilled = extractedFields.filter(function(f) { return Boolean(String(f.value || "").trim()); }).length;
  var mergedTotal = extractedFields.length;
  document.getElementById("stat-fields").textContent = mergedFilled + "/" + mergedTotal;
  document.getElementById("result-fields").textContent = mergedFilled + "/" + mergedTotal;
  document.getElementById("result-accuracy").textContent =
    (mergedTotal ? Math.round(mergedFilled / mergedTotal * 1000) / 10 : 0) + "%";

  showState(stateComplete);
  currentFiles.forEach(function(f) { saveRecentFile(f.name, f.size); });
}

// ── 下载 ──────────────────────────────────────────────────────────────────────

async function handleDownload() {
  if (!downloadUrl) return;
  var token = sessionStorage.getItem('access_token');
  fetch(downloadUrl, {
    headers: token ? { 'Authorization': 'Bearer ' + token } : {}
  })
    .then(function(resp) {
      if (!resp.ok) throw new Error('download failed: ' + resp.status);
      return resp.blob();
    })
    .then(function(blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = downloadFilename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    })
    .catch(function(e) {
      alert(t('download_failed'));
      console.error(e);
    });
}

// ── 重置为初始状态 ────────────────────────────────────────────────────────────

function handleNewFile() {
  downloadUrl     = null;
  extractedFields = [];
  replacementUploadActive = false;
  replacementPreservedFields = {};
  progressBar.style.background = "";
  resetFileUI();

  var tableContainer = document.getElementById("extracted-table-container");
  if (tableContainer) {
    tableContainer.innerHTML = "";
    tableContainer.style.display = "none";
  }
  showState(stateUpload);
}

// ── 保持する項目を選択して再アップロード ──────────────────────────────────────

// 打开隐藏的文件选择框，选择用于重新解析的新文件。
function triggerReplacementUpload() {
  var input = document.getElementById("replacement-file-input");
  if (input) input.click();
}

// 校验新文件，保存用户勾选的字段，然后使用新文件重新执行解析流程。
function handleReplacementFiles(fileList) {
  var validFiles = Array.from(fileList || []).filter(function(f) {
    var ext = f.name.toLowerCase().substring(f.name.lastIndexOf("."));
    return ALLOWED_EXTS.includes(ext);
  });
  if (!validFiles.length) {
    alert(t("alert_pdf"));
    return;
  }

  // 创建保留字段的快照，防止新解析结果覆盖已勾选的值。
  replacementPreservedFields = {};
  extractedFields.forEach(function(f) {
    if (f.preserveOnReupload) {
      replacementPreservedFields[f.id] = Object.assign({}, f);
    }
  });

  // 切换至重新上传模式，复用现有上传和 AI 解析流程。
  replacementUploadActive = true;
  currentFiles = validFiles;

  // 清空 input，确保下次仍可选择同一个文件。
  var input = document.getElementById("replacement-file-input");
  if (input) input.value = "";
  handleUpload();
}

// 将合并后的字段重新写入 Excel，并更新下载链接。
async function refreshReplacementDownload(fields, filename) {
  try {
    var resp = await fetch(API_REGENERATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fields: fields.map(function(f) { return { id: f.id, value: f.value || "" }; }),
        filename: filename,
      }),
    });
    var result = await resp.json();
    if (!resp.ok) throw new Error(result.error || resp.statusText);

    // 后续下载使用包含保留字段和新解析字段的 Excel。
    downloadUrl = API_DOWNLOAD_URL + result.file_id + "?filename=" + encodeURIComponent(result.filename);
    downloadFilename = result.filename;
    return true;
  } catch (err) {
    console.error(err);
    return false;
  }
}

// ── 手动修正后重新生成 Excel ──────────────────────────────────────────────────

async function regenerateExcel(filename) {
  var btn = document.getElementById("regenerate-btn");
  if (!btn || !extractedFields.length) return;

  btn.disabled = true;
  btn.textContent = "⏳ " + t("regenerating");

  try {
    var resp = await fetch(API_REGENERATE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fields: extractedFields.map(function(f) { return { id: f.id, value: f.value || "" }; }),
        filename: filename,
      }),
    });
    var result = await resp.json();
    if (!resp.ok) throw new Error(result.error || resp.statusText);

    downloadUrl      = API_DOWNLOAD_URL + result.file_id + "?filename=" + encodeURIComponent(result.filename);
    downloadFilename = result.filename;

    btn.textContent = "✓ " + t("regenerate_ok");
    btn.classList.add("btn-regenerate-ok");

    // 自动触发下载
    handleDownload();

    setTimeout(function() {
      btn.disabled = false;
      btn.textContent = "↻ " + t("regenerate_btn");
      btn.classList.remove("btn-regenerate-ok");
    }, 3000);

  } catch (err) {
    btn.disabled = false;
    btn.textContent = "✗ Error";
    setTimeout(function() {
      btn.textContent = "↻ " + t("regenerate_btn");
    }, 2000);
  }
}
