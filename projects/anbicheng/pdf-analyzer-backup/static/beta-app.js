/**
 * static/beta-app.js
 * ==================
 * Beta ページのアプリケーションロジック。
 *
 * 依存（グローバル）:
 *   auth.js    → Auth オブジェクト
 *   utils.js   → formatFileSize, getNowStr, escapeHtml, sleep, getTimeStr
 *
 * 公開:
 *   BetaApp.init()  → beta.html のインライン script から呼び出す
 */

var BetaApp = (function () {

  // ── 状態変数 ────────────────────────────────────────────────────────────────
  var sourceFiles   = [];      // ユーザーが選択したソースファイル（PDF/画像）
  var templateId    = null;    // /api/beta/analyze-template で返ったテンプレートID
  var templateExt   = null;    // テンプレート拡張子（.xlsx / .docx）
  var templateFile  = null;    // アップロードしたテンプレート File オブジェクト
  var confirmedFields = [];    // AI 検出 + ユーザー確認済みフィールドリスト
  var extractFields = [];      // テンプレート不使用時の抽出フィールド
  var extractMode   = "keywords"; // "keywords" or "ai-detect"
  var downloadUrl   = null;
  var downloadFilename = null;
  var downloadExt   = ".xlsx";
  var startTime     = null;
  var _lastExtracted = [];    // 最後の抽出結果（PDF確認ビューア用）

  // ── DOM 参照 ────────────────────────────────────────────────────────────────
  var stateUpload, stateProcessing, stateComplete;
  var progressBar, progressPercent, progressText, progressHint, logsBody;

  // ── 初期化 ──────────────────────────────────────────────────────────────────

  function init() {
    stateUpload     = document.getElementById("state-upload");
    stateProcessing = document.getElementById("state-processing");
    stateComplete   = document.getElementById("state-complete");
    progressBar     = document.getElementById("progress-bar");
    progressPercent = document.getElementById("progress-percent");
    progressText    = document.getElementById("progress-text");
    progressHint    = document.getElementById("progress-hint");
    logsBody        = document.getElementById("logs-body");

    _initSourceFiles();
    _initTemplateUpload();
    _initExtractSettings();
    _initButtons();

    // 言語切替
    var lt = document.getElementById("lang-toggle");
    if (lt) lt.addEventListener("click", function () { switchLang(); });
    applyI18n();
  }

  // ── ソースファイル選択 ──────────────────────────────────────────────────────

  function _initSourceFiles() {
    var dropArea  = document.getElementById("drop-area");
    var browseBtn = document.getElementById("browse-btn");
    var fileInput = document.getElementById("file-input");

    browseBtn.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      _addSourceFiles(Array.from(fileInput.files));
      fileInput.value = "";
    });

    ["dragenter", "dragover", "dragleave", "drop"].forEach(function (ev) {
      dropArea.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); });
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      dropArea.addEventListener(ev, function () { dropArea.classList.add("drag-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dropArea.addEventListener(ev, function () { dropArea.classList.remove("drag-over"); });
    });
    dropArea.addEventListener("drop", function (e) {
      var files = Array.from(e.dataTransfer.files).filter(function (f) {
        return f.type === "application/pdf" || f.type.startsWith("image/");
      });
      _addSourceFiles(files);
    });
  }

  function _addSourceFiles(files) {
    files.forEach(function (f) {
      if (!sourceFiles.find(function (x) { return x.name === f.name && x.size === f.size; })) {
        sourceFiles.push(f);
      }
    });
    _renderSourceFileList();
    _updateUploadBtn();
  }

  function _renderSourceFileList() {
    var list = document.getElementById("selected-files-list");
    if (!sourceFiles.length) { list.style.display = "none"; list.innerHTML = ""; return; }

    list.style.display = "block";
    list.innerHTML = sourceFiles.map(function (f, i) {
      var ext = f.name.split(".").pop().toLowerCase();
      var isPdf = ext === "pdf";
      return (
        '<div class="selected-file-item">' +
          '<div class="selected-file-icon ' + (isPdf ? "pdf" : "img") + '">' +
            (isPdf ? "PDF" : "IMG") +
          "</div>" +
          '<div class="selected-file-info">' +
            '<div class="selected-file-name">' + escapeHtml(f.name) + "</div>" +
            '<div class="selected-file-size">' + formatFileSize(f.size) + "</div>" +
          "</div>" +
          '<button class="selected-file-remove" data-idx="' + i + '">✕</button>' +
        "</div>"
      );
    }).join("");

    list.querySelectorAll(".selected-file-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var idx = parseInt(btn.dataset.idx);
        sourceFiles.splice(idx, 1);
        _renderSourceFileList();
        _updateUploadBtn();
      });
    });
  }

  function _updateUploadBtn() {
    document.getElementById("upload-btn").disabled = sourceFiles.length === 0;
  }

  // ── テンプレートアップロード ────────────────────────────────────────────────

  function _initTemplateUpload() {
    var tmplDrop  = document.getElementById("tmpl-drop-area");
    var tmplInput = document.getElementById("tmpl-input");

    tmplDrop.addEventListener("click", function () { tmplInput.click(); });
    tmplInput.addEventListener("change", function () {
      if (tmplInput.files.length) _handleTemplateFile(tmplInput.files[0]);
      tmplInput.value = "";
    });

    ["dragenter", "dragover", "dragleave", "drop"].forEach(function (ev) {
      tmplDrop.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); });
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      tmplDrop.addEventListener(ev, function () { tmplDrop.classList.add("drag-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      tmplDrop.addEventListener(ev, function () { tmplDrop.classList.remove("drag-over"); });
    });
    tmplDrop.addEventListener("drop", function (e) {
      var files = Array.from(e.dataTransfer.files);
      if (files.length) _handleTemplateFile(files[0]);
    });
  }

  function _handleTemplateFile(file) {
    var ext = file.name.split(".").pop().toLowerCase();
    if (!["xlsx", "xls", "docx", "doc"].includes(ext)) {
      alert("テンプレートは .xlsx または .docx ファイルを選択してください。");
      return;
    }
    templateFile = file;

    // 選択済み表示
    var isExcel = ["xlsx", "xls"].includes(ext);
    document.getElementById("tmpl-selected").style.display = "block";
    document.getElementById("tmpl-selected-info").innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="' +
        (isExcel ? "#15803D" : "#1D4ED8") + '" stroke-width="2">' +
        '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>' +
        '<path d="M14 2v6h6"/></svg>' +
      '<span class="template-selected-name">' + escapeHtml(file.name) + "</span>" +
      '<span style="font-size:12px;color:#6B7280;">' + formatFileSize(file.size) + "</span>" +
      '<button class="template-remove-btn" id="tmpl-remove-btn">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
      "</button>";

    document.getElementById("tmpl-remove-btn").addEventListener("click", _removeTemplate);

    // テンプレートドロップゾーンを隠す
    document.getElementById("tmpl-drop-area").style.display = "none";

    // AI 解析開始
    _analyzeTemplate(file);
  }

  function _removeTemplate() {
    templateFile  = null;
    templateId    = null;
    templateExt   = null;
    confirmedFields = [];

    document.getElementById("tmpl-drop-area").style.display = "";
    document.getElementById("tmpl-selected").style.display  = "none";
    document.getElementById("tmpl-analyzing").style.display = "none";
    document.getElementById("field-confirm-panel").style.display  = "none";
    document.getElementById("output-format-indicator").style.display = "none";
  }

  async function _analyzeTemplate(file) {
    document.getElementById("tmpl-analyzing").style.display = "flex";
    document.getElementById("field-confirm-panel").style.display  = "none";
    document.getElementById("output-format-indicator").style.display = "none";

    var formData = new FormData();
    formData.append("template_file", file);

    try {
      var resp = await fetch("/api/beta/analyze-template", {
        method: "POST",
        body: formData,
      });
      var data = await resp.json();

      document.getElementById("tmpl-analyzing").style.display = "none";

      if (!resp.ok) {
        alert("テンプレート解析エラー: " + (data.error || data.detail || resp.statusText));
        _removeTemplate();
        return;
      }

      templateId  = data.template_id;
      templateExt = data.template_ext;

      _renderFieldConfirm(data.fields);
      _renderOutputFormatIndicator(data.template_ext);

    } catch (e) {
      document.getElementById("tmpl-analyzing").style.display = "none";
      alert("テンプレート解析に失敗しました。ネットワーク接続を確認してください。");
      _removeTemplate();
    }
  }

  function _renderFieldConfirm(fields) {
    if (!fields || !fields.length) return;

    var list = document.getElementById("field-confirm-list");
    list.innerHTML = fields.map(function (f) {
      var badgeHtml = f.required
        ? '<span class="field-required-badge">必須</span>'
        : '<span class="field-optional-badge">任意</span>';
      return (
        '<div class="field-confirm-item">' +
          '<input type="checkbox" class="field-checkbox" checked data-id="' + escapeHtml(f.id) + '">' +
          '<span class="field-confirm-label">' + escapeHtml(f.label || f.id) + "</span>" +
          '<span class="field-confirm-desc">' + escapeHtml(f.description || "") + "</span>" +
          badgeHtml +
        "</div>"
      );
    }).join("");

    // チェックボックス変更でカウント更新
    list.querySelectorAll(".field-checkbox").forEach(function (cb) {
      cb.addEventListener("change", _updateFieldCount);
    });

    document.getElementById("field-confirm-panel").style.display = "block";
    _updateFieldCount();

    // すべて選択 / 解除ボタン
    document.getElementById("check-all-btn").onclick = function () {
      list.querySelectorAll(".field-checkbox").forEach(function (cb) { cb.checked = true; });
      _updateFieldCount();
    };
    document.getElementById("uncheck-all-btn").onclick = function () {
      list.querySelectorAll(".field-checkbox").forEach(function (cb) { cb.checked = false; });
      _updateFieldCount();
    };

    // fields を保持（ID で後から参照）
    confirmedFields = fields;
  }

  function _updateFieldCount() {
    var list = document.getElementById("field-confirm-list");
    var checked = list.querySelectorAll(".field-checkbox:checked").length;
    document.getElementById("field-selected-count").textContent = checked;
  }

  function _getCheckedFields() {
    var list = document.getElementById("field-confirm-list");
    if (!list || !confirmedFields.length) return [];
    var checkedIds = new Set();
    list.querySelectorAll(".field-checkbox:checked").forEach(function (cb) {
      checkedIds.add(cb.dataset.id);
    });
    return confirmedFields.filter(function (f) { return checkedIds.has(f.id); });
  }

  function _renderOutputFormatIndicator(ext) {
    var isWord  = ext && (ext === ".docx" || ext === ".doc");
    var isExcel = ext && (ext === ".xlsx" || ext === ".xls");
    var el = document.getElementById("output-format-indicator");

    var label = isWord
      ? "Word (.docx) 形式で出力します"
      : isExcel
        ? "Excel (.xlsx) 形式で出力します"
        : "デフォルト Excel テンプレートで出力します";
    var colorClass = isWord ? "word" : "";

    el.className = "output-format-indicator " + colorClass;
    el.innerHTML =
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>' +
      "<span>" + label + "</span>";
    el.style.display = "flex";
  }

  // ── 抽出設定モーダル ─────────────────────────────────────────────────────

  function _initExtractSettings() {
    var modal = document.getElementById("extract-modal");
    if (!modal) return;

    // モード切替タブ
    var tabKeywords = document.getElementById("tab-keywords");
    var tabAiDetect = document.getElementById("tab-ai-detect");
    tabKeywords.addEventListener("click", function () {
      extractMode = "keywords";
      tabKeywords.classList.add("active");
      tabAiDetect.classList.remove("active");
      document.getElementById("mode-keywords").style.display = "";
      document.getElementById("mode-ai-detect").style.display = "none";
    });
    tabAiDetect.addEventListener("click", function () {
      extractMode = "ai-detect";
      tabAiDetect.classList.add("active");
      tabKeywords.classList.remove("active");
      document.getElementById("mode-keywords").style.display = "none";
      document.getElementById("mode-ai-detect").style.display = "";
    });

    // キーワードサンプルチップクリック
    document.querySelectorAll(".keyword-example-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var ta = document.getElementById("keyword-textarea");
        var kw = chip.dataset.kw;
        var current = ta.value.trim();
        var lines = current ? current.split(/[\n,]/).map(function (s) { return s.trim(); }) : [];
        if (lines.indexOf(kw) === -1) {
          ta.value = current ? current + "\n" + kw : kw;
        }
        _updateModalConfirmBtn();
      });
    });

    // キーワード入力時にも確認ボタンを更新
    var ta = document.getElementById("keyword-textarea");
    if (ta) {
      ta.addEventListener("input", _updateModalConfirmBtn);
    }

    // AI 自動検出ボタン
    document.getElementById("ai-detect-btn").addEventListener("click", _doAiDetect);

    // すべて選択 / 解除
    document.getElementById("extract-check-all-btn").addEventListener("click", function () {
      document.getElementById("extract-field-confirm-list")
        .querySelectorAll(".field-checkbox").forEach(function (cb) { cb.checked = true; });
      _updateExtractFieldCount();
    });
    document.getElementById("extract-uncheck-all-btn").addEventListener("click", function () {
      document.getElementById("extract-field-confirm-list")
        .querySelectorAll(".field-checkbox").forEach(function (cb) { cb.checked = false; });
      _updateExtractFieldCount();
    });

    // モーダル閉じるボタン
    document.getElementById("modal-close-btn").addEventListener("click", _closeModal);
    document.getElementById("modal-cancel-btn").addEventListener("click", _closeModal);

    // 確認ボタン → 処理開始
    document.getElementById("modal-confirm-btn").addEventListener("click", function () {
      if (extractMode === "keywords") {
        // キーワードモード → テキストをそのまま AI プロンプトとして送信
        var promptText = document.getElementById("keyword-textarea").value.trim();
        if (!promptText) {
          alert(t("extract_alert_input_required"));
          return;
        }
        _closeModal();
        _doProcess([], promptText);
      } else {
        // AI 自動検出モード → 検出済みフィールドで処理
        if (extractFields.length === 0) {
          alert(t("extract_alert_run_ai_first"));
          return;
        }
        var fieldsToUse = _getExtractCheckedFields();
        if (fieldsToUse.length === 0) {
          alert(t("extract_alert_pick_one"));
          return;
        }
        _closeModal();
        _doProcess(fieldsToUse, null);
      }
    });

    // オーバーレイクリックで閉じる
    modal.addEventListener("click", function (e) {
      if (e.target === modal) _closeModal();
    });
  }

  function _openModal() {
    document.getElementById("extract-modal").classList.add("active");
    document.body.style.overflow = "hidden";
    _updateModalConfirmBtn();
  }

  function _closeModal() {
    document.getElementById("extract-modal").classList.remove("active");
    document.body.style.overflow = "";
  }

  function _resetModal() {
    extractFields = [];
    extractMode = "keywords";
    var ta = document.getElementById("keyword-textarea");
    if (ta) ta.value = "";
    var panel = document.getElementById("extract-field-confirm-panel");
    if (panel) panel.style.display = "none";
    var analyzing = document.getElementById("pdf-analyzing");
    if (analyzing) analyzing.style.display = "none";
    var tabKw = document.getElementById("tab-keywords");
    var tabAi = document.getElementById("tab-ai-detect");
    if (tabKw) tabKw.classList.add("active");
    if (tabAi) tabAi.classList.remove("active");
    var modeKw = document.getElementById("mode-keywords");
    var modeAi = document.getElementById("mode-ai-detect");
    if (modeKw) modeKw.style.display = "";
    if (modeAi) modeAi.style.display = "none";
    _closeModal();
  }

  function _updateModalConfirmBtn() {
    var btn = document.getElementById("modal-confirm-btn");
    if (!btn) return;
    if (extractMode === "keywords") {
      var ta = document.getElementById("keyword-textarea");
      var hasKeywords = ta && ta.value.trim().length > 0;
      var hasFields = extractFields.length > 0;
      btn.disabled = !hasKeywords && !hasFields;
      btn.textContent = hasFields ? t("extract_confirm_btn") : t("extract_confirm_btn_keywords");
    } else {
      btn.disabled = extractFields.length === 0;
      btn.textContent = t("extract_confirm_btn");
    }
  }

  async function _doAiDetect() {
    if (!sourceFiles.length) {
      alert(t("extract_alert_upload_pdf"));
      return;
    }

    var aiBtn = document.getElementById("ai-detect-btn");
    var analyzing = document.getElementById("pdf-analyzing");
    aiBtn.disabled = true;
    analyzing.style.display = "flex";
    document.getElementById("extract-field-confirm-panel").style.display = "none";

    var formData = new FormData();
    formData.append("pdf_file", sourceFiles[0]);

    try {
      var resp = await fetch("/api/beta/analyze-pdf", {
        method: "POST",
        body: formData,
      });
      var data = await resp.json();
      analyzing.style.display = "none";
      aiBtn.disabled = false;

      if (!resp.ok) {
        alert(t("extract_alert_pdf_error") + (data.error || data.detail || resp.statusText));
        return;
      }

      if (data.fields && data.fields.length) {
        extractFields = data.fields;
        _renderExtractFieldConfirm(data.fields);
        document.getElementById("extract-field-title").textContent =
          t("extract_field_list_title_ai");
        _updateModalConfirmBtn();
      } else {
        alert(t("extract_alert_no_fields"));
      }
    } catch (e) {
      analyzing.style.display = "none";
      aiBtn.disabled = false;
      alert(t("extract_alert_pdf_network"));
    }
  }

  function _renderExtractFieldConfirm(fields) {
    if (!fields || !fields.length) return;

    var list = document.getElementById("extract-field-confirm-list");
    list.innerHTML = fields.map(function (f) {
      var badgeHtml = f.required
        ? '<span class="field-required-badge">' + escapeHtml(t("extract_required_badge")) + '</span>'
        : '<span class="field-optional-badge">' + escapeHtml(t("extract_optional_badge")) + '</span>';
      return (
        '<div class="field-confirm-item">' +
          '<input type="checkbox" class="field-checkbox" checked data-id="' + escapeHtml(f.id) + '">' +
          '<span class="field-confirm-label">' + escapeHtml(f.label || f.id) + '</span>' +
          '<span class="field-confirm-desc">' + escapeHtml(f.description || "") + '</span>' +
          badgeHtml +
        '</div>'
      );
    }).join("");

    list.querySelectorAll(".field-checkbox").forEach(function (cb) {
      cb.addEventListener("change", _updateExtractFieldCount);
    });

    document.getElementById("extract-field-confirm-panel").style.display = "block";
    _updateExtractFieldCount();
  }

  function _updateExtractFieldCount() {
    var list = document.getElementById("extract-field-confirm-list");
    var checked = list.querySelectorAll(".field-checkbox:checked").length;
    document.getElementById("extract-field-selected-count").textContent = checked;
  }

  function _getExtractCheckedFields() {
    var list = document.getElementById("extract-field-confirm-list");
    if (!list || !extractFields.length) return [];
    var checkedIds = new Set();
    list.querySelectorAll(".field-checkbox:checked").forEach(function (cb) {
      checkedIds.add(cb.dataset.id);
    });
    return extractFields.filter(function (f) { return checkedIds.has(f.id); });
  }

  // ── ボタン初期化 ────────────────────────────────────────────────────────────

  function _initButtons() {
    document.getElementById("upload-btn").addEventListener("click", _startProcess);
    document.getElementById("download-btn").addEventListener("click", _doDownload);
    document.getElementById("new-file-btn").addEventListener("click", _resetToUpload);
  }

  // ── 処理開始 ────────────────────────────────────────────────────────────────

  async function _startProcess() {
    if (!sourceFiles.length) return;

    if (templateId) {
      // テンプレートあり → テンプレート解析のフィールドで直接処理
      _doProcess(_getCheckedFields(), null);
    } else {
      // テンプレートなし → モーダルを開いてフィールド設定を求める
      _openModal();
    }
  }

  async function _doProcess(fieldsToUse, userPrompt) {
    startTime = Date.now();
    _showState(stateProcessing);

    var totalSize = sourceFiles.reduce(function (s, f) { return s + f.size; }, 0);
    var displayName = sourceFiles.length === 1
      ? sourceFiles[0].name
      : sourceFiles.length + " 件のファイル";

    document.getElementById("proc-name").textContent = displayName;
    document.getElementById("stat-size").textContent   = formatFileSize(totalSize);
    document.getElementById("stat-pages").textContent  = "--";
    document.getElementById("stat-fields").textContent = "--";

    _clearLogs();
    _setProgress(5, "ファイルをアップロード中...", "残り時間：計算中...");
    _addLog("SUCCESS", "アップロード開始 (" + sourceFiles.length + " 件)", "success");

    await sleep(300);
    _setProgress(15, "PDF を解析中...", "残り時間：計算中...");
    _addLog("INFO", "ファイルのメタ情報を確認中...", "info");

    await sleep(500);
    _setProgress(25, "テキストを抽出中...", "残り約 15 秒");
    _addLog("INFO", "テキスト層の確認中...", "info");

    // FormData 構築
    var formData = new FormData();
    sourceFiles.forEach(function (f) { formData.append("pdf_files", f); });
    formData.append("fields_json", JSON.stringify(fieldsToUse));
    if (userPrompt)  formData.append("user_prompt", userPrompt);
    if (templateId)  formData.append("template_id",  templateId);
    if (templateExt) formData.append("template_ext", templateExt);

    try {
      _setProgress(35, "AI が認識中...", "残り約 12 秒");
      _addLog("INFO", "Claude AI にデータを送信中...", "info");

      // 時間経過に応じた処理ログ
      var aiLogs = [
        { sec: 2,  msg: "PDFを高画質画像に変換中...",          type: "info" },
        { sec: 4,  msg: "AIモデルに画像データを送信中...",      type: "info" },
        { sec: 7,  msg: "ページのレイアウトと構造を解析中...", type: "info" },
        { sec: 11, msg: "フィールド値を抽出中...",             type: "info" },
        { sec: 16, msg: "抽出結果を検証中...",                 type: "info" },
        { sec: 21, msg: "テンプレートへの書き込みを準備中...", type: "info" },
      ];
      var aiLogsFired = [];

      // AI 処理中の疑似プログレス＋経過時間＋ログ
      var progressInterval = setInterval(function () {
        var cur = parseInt(progressBar.style.width) || 35;
        var elapsed = Math.round((Date.now() - startTime) / 1000);
        if (cur < 85) {
          var next = Math.min(85, Math.round(cur + Math.random() * 5 + 1));
          _setProgress(next, "AI が解析中...", "残り約 " + Math.max(3, 20 - elapsed) + " 秒");
        }
        var elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
        document.getElementById("result-time") && (document.getElementById("result-time").textContent = elapsedSec + "s");
        aiLogs.forEach(function (entry) {
          if (elapsed >= entry.sec && aiLogsFired.indexOf(entry.sec) === -1) {
            aiLogsFired.push(entry.sec);
            _addLog("INFO", entry.msg, entry.type);
          }
        });
      }, 1000);

      var resp = await fetch("/api/beta/process", { method: "POST", body: formData });
      clearInterval(progressInterval);

      var contentType = resp.headers.get("Content-Type") || "";

      if (!resp.ok) {
        _setProgress(100, "処理に失敗しました", "");
        _addLog("ERROR", "処理エラー", "warn");
        progressBar.style.background = "#EF4444";
        if (contentType.includes("application/json")) {
          var err = await resp.json();
          var errMsg = err.error ||
            (Array.isArray(err.detail)
              ? err.detail.map(function (d) { return d.msg || JSON.stringify(d); }).join("; ")
              : err.detail) ||
            resp.statusText;
          _addLog("ERROR", errMsg, "warn");
        } else {
          _addLog("ERROR", resp.status + " " + resp.statusText, "warn");
        }
        return;
      }

      _setProgress(90, "テンプレートに書き込み中...", "もうすぐ完了...");
      _addLog("SUCCESS", "AI 抽出完了。テンプレートに書き込み中...", "success");

      if (contentType.includes("application/json")) {
        var result = await resp.json();
        await _applyResult(result);
      } else {
        _addLog("ERROR", "予期しないレスポンス形式です", "warn");
      }

    } catch (e) {
      console.error(e);
      _setProgress(100, "リクエストに失敗しました", "");
      progressBar.style.background = "#EF4444";
      _addLog("ERROR", "ネットワークエラー: " + e.message, "warn");
    }
  }

  async function _applyResult(result) {
    var fieldsFilled = String(result.fields_filled || 0);
    var fieldsTotal  = String(result.fields_total  || 0);
    var pageCount    = String(result.page_count    || "--");

    document.getElementById("stat-pages").textContent  = pageCount;
    document.getElementById("stat-fields").textContent = fieldsFilled + "/" + fieldsTotal;

    downloadExt      = result.file_ext || ".xlsx";
    downloadUrl      = "/api/beta/download/" + result.file_id +
      "?filename=" + encodeURIComponent(result.filename) +
      "&ext=" + encodeURIComponent(downloadExt);
    downloadFilename = result.filename || ("filled" + downloadExt);

    // ファイルごとの処理結果ログ（複数ファイルの場合）
    if (result.file_results && result.file_results.length > 1) {
      result.file_results.forEach(function (fr) {
        _addLog("INFO", fr.filename + " — " + fr.pages + "ページ / " + fr.fields_filled + "項目入力", "info");
      });
    }

    await sleep(300);
    _setProgress(100, "処理完了", "");
    _addLog(
      "SUCCESS",
      "完了: " + fieldsFilled + "/" + fieldsTotal + " フィールドを抽出" +
      "　(" + pageCount + "ページ / 入力率" + (Number(fieldsTotal) > 0 ? Math.round(Number(fieldsFilled) / Number(fieldsTotal) * 100) : 0) + "%)",
      "success"
    );
    await sleep(600);

    // 完了画面を更新
    var accuracyVal = Number(fieldsTotal) > 0
      ? Math.round((Number(fieldsFilled) / Number(fieldsTotal)) * 1000) / 10
      : 0;
    document.getElementById("result-accuracy").textContent = accuracyVal + "%";

    var resultFilename = sourceFiles.length === 1
      ? sourceFiles[0].name
      : sourceFiles.map(function (f) { return f.name; }).join(", ");
    var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

    document.getElementById("result-fields").textContent   = fieldsFilled + "/" + fieldsTotal;
    document.getElementById("result-time").textContent     = elapsed + "s";
    document.getElementById("result-filename").textContent = resultFilename;
    document.getElementById("result-date").textContent     = getNowStr();

    // 出力形式バッジ
    var isWord  = downloadExt === ".docx";
    var isExcel = downloadExt === ".xlsx";
    var fmtBadge =
      '<span class="result-output-badge ' + (isWord ? "word" : "excel") + '">' +
        (isWord ? "Word (.docx)" : "Excel (.xlsx)") +
      "</span>";
    document.getElementById("result-output-format").innerHTML = fmtBadge;

    // ダウンロードボタンのラベル
    var btnLabel = isWord
      ? "記入済み Word をダウンロード"
      : "記入済み Excel をダウンロード";
    document.getElementById("download-btn-label").textContent = btnLabel;
    if (isWord) {
      document.getElementById("download-btn").style.background =
        "linear-gradient(135deg, #1D4ED8, #2563EB)";
    }

    // 抽出データテーブル
    _lastExtracted = result.extracted || [];
    _renderExtractedTable(_lastExtracted, result.filename);

    _showState(stateComplete);
  }

  // ── ダウンロード ────────────────────────────────────────────────────────────

  async function _doDownload() {
    if (!downloadUrl) return;
    var token = sessionStorage.getItem('access_token');
    fetch(downloadUrl, {
      headers: token ? { 'Authorization': 'Bearer ' + token } : {}
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('download failed: ' + resp.status);
        return resp.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = downloadFilename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch(function (e) {
        alert('ダウンロードに失敗しました。再度お試しください。');
        console.error(e);
      });
  }

  // ── リセット ────────────────────────────────────────────────────────────────

  function _resetToUpload() {
    sourceFiles      = [];
    extractFields    = [];
    extractMode      = "keywords";
    downloadUrl      = null;
    downloadFilename = null;
    downloadExt      = ".xlsx";
    progressBar.style.background = "";

    _renderSourceFileList();
    _updateUploadBtn();
    _removeTemplate();
    _resetModal();

    var tableContainer = document.getElementById("extracted-table-container");
    if (tableContainer) { tableContainer.innerHTML = ""; tableContainer.style.display = "none"; }

    document.getElementById("download-btn").style.background = "";
    _showState(stateUpload);
  }

  // ── 抽出データテーブル ──────────────────────────────────────────────────────

  function _renderExtractedTable(fields, outputFilename) {
    var container = document.getElementById("extracted-table-container");
    if (!container || !fields.length) return;

    var filledCount = fields.filter(function (f) { return f.filled; }).length;
    var totalCount  = fields.length;
    var hasSource   = fields.some(function (f) { return f.source; });
    var hasNotes    = fields.some(function (f) { return f.note; });

    var rows = fields.map(function (f, i) {
      var statusClass = f.filled ? "status-filled" : "status-empty";
      var statusText  = f.filled ? "入力済" : "未入力";
      var noteHtml    = f.note
        ? '<span class="note-badge">⚠ 推定</span><span class="note-text">' + escapeHtml(f.note) + "</span>"
        : "";
      return (
        '<tr class="' + (f.filled ? "" : "row-empty") + (f.note ? " row-inferred" : "") + '">' +
        "<td>" + (i + 1) + "</td>" +
        '<td class="field-label">' + escapeHtml(f.label || f.id) + "</td>" +
        '<td class="field-value-cell">' +
          '<input type="text" class="field-edit-input' + (f.filled ? "" : " field-edit-empty") + '"' +
          ' data-id="' + escapeHtml(f.id) + '"' +
          ' value="' + escapeHtml(f.value || "") + '"' +
          ' placeholder="' + (f.filled ? "" : "値を入力") + '">' +
        "</td>" +
        (hasSource ? '<td class="field-source">' + escapeHtml(f.source || "-") + "</td>" : "") +
        '<td class="field-status"><span class="status-badge-sm ' + statusClass + '" data-status="' + escapeHtml(f.id) + '">' + statusText + "</span></td>" +
        (hasNotes ? '<td class="field-note">' + noteHtml + "</td>" : "") +
        "</tr>"
      );
    }).join("");

    var html =
      '<div class="extracted-table-header">' +
        "<h3>抽出データ確認</h3>" +
        '<span class="extracted-summary">' + filledCount + "/" + totalCount + "</span>" +
      "</div>" +
      '<div class="extracted-table-wrap">' +
        '<table class="extracted-table">' +
          "<thead><tr>" +
            "<th>#</th><th>フィールド</th><th>値</th>" +
            (hasSource ? "<th>ソース</th>" : "") +
            "<th>状態</th>" +
            (hasNotes ? "<th>備考</th>" : "") +
          "</tr></thead>" +
          "<tbody>" + rows + "</tbody>" +
        "</table>" +
      "</div>";

    container.innerHTML = html;
    container.style.display = "block";

    // 入力変更でステータスバッジを更新
    container.querySelectorAll(".field-edit-input").forEach(function (input) {
      input.addEventListener("input", function () {
        var badge = container.querySelector('[data-status="' + input.dataset.id + '"]');
        if (!badge) return;
        if (input.value.trim()) {
          badge.className = "status-badge-sm status-filled";
          badge.textContent = "入力済";
          input.classList.remove("field-edit-empty");
        } else {
          badge.className = "status-badge-sm status-empty";
          badge.textContent = "未入力";
          input.classList.add("field-edit-empty");
        }
      });
    });
  }

  // ── UI ユーティリティ ───────────────────────────────────────────────────────

  function _showState(state) {
    [stateUpload, stateProcessing, stateComplete].forEach(function (el) {
      el.classList.remove("active");
    });
    state.classList.add("active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function _setProgress(percent, text, hint) {
    progressBar.style.width = percent + "%";
    progressPercent.textContent = percent + "%";
    if (text) progressText.textContent = text;
    if (hint) progressHint.textContent = hint;
  }

  function _clearLogs() {
    logsBody.innerHTML = "";
  }

  function _addLog(tag, msg, type) {
    type = type || "info";
    var empty = logsBody.querySelector(".log-empty");
    if (empty) empty.remove();
    var line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML =
      '<span class="log-time">' + getTimeStr() + "</span>" +
      '<span class="log-tag log-tag-' + type + '">[' + tag + "]</span>" +
      '<span class="log-msg">' + escapeHtml(msg) + "</span>";
    logsBody.appendChild(line);
    logsBody.scrollTop = logsBody.scrollHeight;
  }

  // ── 公開インターフェース ────────────────────────────────────────────────────

  return {
    init: init,
    getSourceFiles: function () { return sourceFiles; },
    getExtractedFields: function () { return _lastExtracted || []; },
  };

})();
