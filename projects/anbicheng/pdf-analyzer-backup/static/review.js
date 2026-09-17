/**
 * static/review.js
 * ================
 * PDF 確認ビューア — 抽出フィールドのハイライト表示。
 *
 * 依存: auth.js (Auth.getToken), i18n.js (t), app.js (currentFiles, extractedFields)
 *
 * 公開 API:
 *   openReviewViewer()  — モーダルを開いて PDF レビューを開始
 *   closeReviewViewer() — モーダルを閉じる
 */

/* global Auth, t, currentFiles, extractedFields */

// ── 定数 ────────────────────────────────────────────────────────────────────

var REVIEW_API_URL = (window.API_PREFIX || "/api") + "/review";

// ハイライトカラーパレット（8色）
var REVIEW_COLORS = [
  { border: "#2563EB", bg: "rgba(37,99,235,0.12)",  hover: "rgba(37,99,235,0.25)"  },  // blue
  { border: "#DC2626", bg: "rgba(220,38,38,0.12)",   hover: "rgba(220,38,38,0.25)"  },  // red
  { border: "#059669", bg: "rgba(5,150,105,0.12)",   hover: "rgba(5,150,105,0.25)"  },  // green
  { border: "#D97706", bg: "rgba(217,119,6,0.12)",   hover: "rgba(217,119,6,0.25)"  },  // amber
  { border: "#7C3AED", bg: "rgba(124,58,237,0.12)",  hover: "rgba(124,58,237,0.25)" },  // violet
  { border: "#DB2777", bg: "rgba(219,39,119,0.12)",  hover: "rgba(219,39,119,0.25)" },  // pink
  { border: "#0891B2", bg: "rgba(8,145,178,0.12)",   hover: "rgba(8,145,178,0.25)"  },  // cyan
  { border: "#65A30D", bg: "rgba(101,163,13,0.12)",  hover: "rgba(101,163,13,0.25)" },  // lime
];

// ── 状態 ────────────────────────────────────────────────────────────────────

var _reviewCache = {};       // filename → {pages: [...]} のキャッシュ
var _activeTooltip = null;   // 現在表示中のツールチップ要素
var _fieldColorMap = {};     // field_id → color index


// ── ページ判定ヘルパー（メイン or Beta） ─────────────────────────────────────

function _getReviewFiles() {
  // メインページ: currentFiles グローバル
  if (typeof currentFiles !== "undefined" && currentFiles && currentFiles.length > 0) {
    return currentFiles;
  }
  // Beta ページ: BetaApp.getSourceFiles()
  if (typeof BetaApp !== "undefined" && BetaApp.getSourceFiles) {
    var betaFiles = BetaApp.getSourceFiles();
    if (betaFiles && betaFiles.length > 0) return betaFiles;
  }
  return [];
}

function _getReviewFields() {
  // メインページ: extractedFields グローバル
  if (typeof extractedFields !== "undefined" && extractedFields && extractedFields.length > 0) {
    return extractedFields;
  }
  // Beta ページ: BetaApp.getExtractedFields()
  if (typeof BetaApp !== "undefined" && BetaApp.getExtractedFields) {
    var betaFields = BetaApp.getExtractedFields();
    if (betaFields && betaFields.length > 0) return betaFields;
  }
  return [];
}


// ── モーダル制御 ─────────────────────────────────────────────────────────────

function openReviewViewer() {
  var overlay = document.getElementById("review-overlay");
  if (!overlay) return;

  overlay.classList.add("active");
  document.body.style.overflow = "hidden";

  // ESC キーで閉じる
  document.addEventListener("keydown", _reviewEscHandler);

  // ファイルを処理
  _processReviewFiles();
}

function closeReviewViewer() {
  var overlay = document.getElementById("review-overlay");
  if (!overlay) return;

  overlay.classList.remove("active");
  document.body.style.overflow = "";
  _hideTooltip();
  document.removeEventListener("keydown", _reviewEscHandler);
}

function _reviewEscHandler(e) {
  if (e.key === "Escape") closeReviewViewer();
}


// ── ファイル処理 ─────────────────────────────────────────────────────────────

function _processReviewFiles() {
  var body = document.getElementById("review-body");
  if (!body) return;

  // ローディング表示
  body.innerHTML =
    '<div class="review-loading">' +
      '<div class="review-spinner"></div>' +
      '<div class="review-loading-text">' + t("review_loading") + '</div>' +
    '</div>';

  // ファイル取得（メイン or Beta）
  var files = _getReviewFiles();
  if (!files || files.length === 0) {
    body.innerHTML =
      '<div class="review-loading">' +
        '<div class="review-loading-text">' + t("review_no_file") + '</div>' +
      '</div>';
    return;
  }

  // フィールド色マップを構築
  _buildFieldColorMap();

  // 全ファイルを並列処理
  var promises = [];
  for (var i = 0; i < files.length; i++) {
    promises.push(_fetchReviewData(files[i]));
  }

  Promise.all(promises).then(function(results) {
    _renderAllFiles(body, files, results);
  }).catch(function(err) {
    body.innerHTML =
      '<div class="review-loading">' +
        '<div class="review-loading-text" style="color:#DC2626">Error: ' +
        (err.message || err) + '</div>' +
      '</div>';
  });
}


function _buildFieldColorMap() {
  _fieldColorMap = {};
  var fields = _getReviewFields();
  var colorIdx = 0;
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].value) {
      _fieldColorMap[fields[i].id] = colorIdx % REVIEW_COLORS.length;
      colorIdx++;
    }
  }
}


function _fetchReviewData(file) {
  // キャッシュチェック
  if (_reviewCache[file.name]) {
    return Promise.resolve(_reviewCache[file.name]);
  }

  var fields = _getReviewFields();

  // フィールド情報を軽量化して送信
  var fieldsForApi = [];
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].value) {
      fieldsForApi.push({
        id: fields[i].id,
        label: fields[i].label,
        cell: fields[i].cell || "",
        value: fields[i].value,
      });
    }
  }

  var formData = new FormData();
  formData.append("file", file);
  formData.append("fields_json", JSON.stringify(fieldsForApi));
  // 契約書のみモード: AI 判定の向きに表示を回転（上向き）
  if (typeof reviewRotations !== "undefined" && reviewRotations && reviewRotations[file.name]) {
    formData.append("rotate", reviewRotations[file.name]);
  }

  var headers = {};
  var token = Auth.getToken();
  if (token) headers["Authorization"] = "Bearer " + token;

  return fetch(REVIEW_API_URL, {
    method: "POST",
    headers: headers,
    body: formData,
  })
  .then(function(res) {
    if (!res.ok) throw new Error("Review API error: " + res.status);
    return res.json();
  })
  .then(function(data) {
    // 契約書のみモード: 検出された契約書ページのみ残す
    if (typeof reviewPageFilter !== "undefined" && reviewPageFilter &&
        reviewPageFilter[file.name] !== undefined && data.pages) {
      var targetPage = reviewPageFilter[file.name];
      data.pages = data.pages.filter(function(p) { return p.page_num === targetPage; });
    }
    _reviewCache[file.name] = data;
    return data;
  });
}

// ── レンダリング ─────────────────────────────────────────────────────────────

function _renderAllFiles(container, files, results) {
  container.innerHTML = "";

  // ハイライト数の集計
  var totalHighlights = 0;
  for (var r = 0; r < results.length; r++) {
    var pages = results[r].pages || [];
    for (var p = 0; p < pages.length; p++) {
      totalHighlights += (pages[p].highlights || []).length;
    }
  }

  // ヘッダーバッジ更新
  var badge = document.getElementById("review-header-badge");
  if (badge) {
    badge.textContent = totalHighlights + " " + t("review_highlights_found");
  }

  for (var i = 0; i < files.length; i++) {
    var section = document.createElement("div");
    section.className = "review-file-section";

    // ファイル名ラベル（複数ファイル時のみ）
    if (files.length > 1) {
      var nameLabel = document.createElement("div");
      nameLabel.className = "review-file-name";
      nameLabel.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
          '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>' +
          '<polyline points="14 2 14 8 20 8"/>' +
        '</svg>' +
        _escapeHtml(files[i].name);
      section.appendChild(nameLabel);
    }

    // ページをレンダリング
    var data = results[i];
    var pages = data.pages || [];
    for (var j = 0; j < pages.length; j++) {
      section.appendChild(_renderPage(pages[j], pages.length));
    }

    container.appendChild(section);
  }

  // フィールド一覧チップを上部に追加（クリックで該当の枠だけ表示）
  _buildFieldChips(container);
}


/**
 * 抽出フィールドのチップ一覧を review-body 上部に作る。
 * 一覧は抽出結果（値あり）全件から作るので、OCR で位置特定できない項目
 * （例: 賃借人の氏名）も「項目名: 値」として必ず表示される。
 * OCR で位置特定できた項目はクリックで原本上の枠を表示できる（カラードット）。
 */
function _buildFieldChips(container) {
  var isZh = (typeof currentLang !== "undefined" && currentLang === "zh");

  var fields = _getReviewFields().filter(function (f) { return f.value; });
  if (!fields.length) return;

  // OCR で位置特定できた field_id → 色
  var locatable = {};
  container.querySelectorAll(".review-highlight").forEach(function (h) {
    locatable[h.getAttribute("data-field-id")] = h.getAttribute("data-color") || "#2563EB";
  });

  var bar = document.createElement("div");
  bar.className = "review-chip-bar";
  bar.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;padding:12px 16px;" +
    "border-bottom:1px solid #E2E8F0;position:sticky;top:0;background:#fff;z-index:5;";

  var hint = document.createElement("div");
  hint.className = "review-chip-hint";
  hint.textContent = isZh
    ? "提取结果一览（点彩色圆点的字段可在原文中定位；灰点=OCR 未能定位）"
    : "抽出結果一覧（カラードットの項目は原本で位置表示できます／灰色=OCR で特定不可）";
  hint.style.cssText = "width:100%;font-size:12px;color:#64748B;margin-bottom:2px;";
  bar.appendChild(hint);

  fields.forEach(function (f) {
    var hasBox = !!locatable[f.id];
    var color = hasBox ? locatable[f.id] : "#CBD5E1";
    var valShort = String(f.value);
    if (valShort.length > 24) valShort = valShort.slice(0, 24) + "…";

    var chip = document.createElement("button");
    chip.className = "review-chip";
    chip.setAttribute("data-field-id", f.id);
    chip.style.cssText = "display:inline-flex;align-items:center;gap:6px;padding:5px 10px;" +
      "border:1px solid #E2E8F0;border-radius:999px;background:#F8FAFC;font-size:12px;" +
      "cursor:pointer;color:#1E293B;line-height:1.2;max-width:100%;";
    chip.innerHTML =
      '<span style="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:' + color + '"></span>' +
      '<span style="font-weight:600">' + _escapeHtml(f.label) + '</span>' +
      '<span style="color:#475569">: ' + _escapeHtml(valShort) + '</span>';
    chip.addEventListener("click", function () {
      _showOnlyField(container, f.id, chip, hasBox, isZh);
    });
    bar.appendChild(chip);
  });

  container.insertBefore(bar, container.firstChild);
}


/** 指定フィールドのハイライト枠だけを表示し、最初の1件を中央へスクロール。 */
function _showOnlyField(container, fieldId, chipEl, hasBox, isZh) {
  container.querySelectorAll(".review-highlight").forEach(function (h) {
    h.style.display = (h.getAttribute("data-field-id") === fieldId) ? "block" : "none";
  });

  container.querySelectorAll(".review-chip").forEach(function (c) {
    c.style.background = "#F8FAFC";
    c.style.borderColor = "#E2E8F0";
    c.style.fontWeight = "400";
  });
  if (chipEl) {
    chipEl.style.background = "#EFF6FF";
    chipEl.style.borderColor = "#2563EB";
  }

  var hint = container.querySelector(".review-chip-hint");
  if (hasBox) {
    var esc = (window.CSS && CSS.escape) ? CSS.escape(fieldId) : fieldId;
    var first = container.querySelector('.review-highlight[data-field-id="' + esc + '"]');
    if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
  } else if (hint) {
    hint.textContent = isZh
      ? "该字段 OCR 未能在原文中定位（仅能显示提取值）"
      : "この項目は原本での位置を特定できませんでした（抽出値のみ表示）";
  }
}


function _renderPage(pageData, totalPages) {
  var wrap = document.createElement("div");
  wrap.className = "review-page-wrap";

  // ページラベル
  var label = document.createElement("div");
  label.className = "review-page-label";

  var labelLeft = '<span class="review-page-label-left">' +
    t("review_page") + " " + (pageData.page_num + 1) + " / " + totalPages +
    '</span>';

  var labelRight = "";
  if (!pageData.has_text_layer) {
    labelRight = '<span class="review-no-text-badge">' + t("review_no_text") + '</span>';
  } else {
    var hlCount = (pageData.highlights || []).length;
    if (hlCount > 0) {
      labelRight = '<span style="color:#059669;font-weight:500">' +
        hlCount + ' ' + t("review_fields_found") + '</span>';
    }
  }

  label.innerHTML = labelLeft + labelRight;
  wrap.appendChild(label);

  // ページ画像コンテナ
  var pageContainer = document.createElement("div");
  pageContainer.className = "review-page-container";

  // 画像
  var img = document.createElement("img");
  img.className = "review-page-image";
  img.src = "data:image/jpeg;base64," + pageData.image_b64;
  img.alt = "Page " + (pageData.page_num + 1);
  img.draggable = false;
  pageContainer.appendChild(img);

  // ハイライト矩形
  var highlights = pageData.highlights || [];
  for (var i = 0; i < highlights.length; i++) {
    var hl = highlights[i];
    var colorIdx = _fieldColorMap[hl.field_id] !== undefined
      ? _fieldColorMap[hl.field_id]
      : (i % REVIEW_COLORS.length);
    var color = REVIEW_COLORS[colorIdx];

    var hlDiv = document.createElement("div");
    hlDiv.className = "review-highlight";

    // デフォルトは非表示。フィールドのチップをクリックした時だけ表示する
    // （OCR で特定できた位置のみ。全件同時表示は雑然とするため）
    hlDiv.style.display = "none";

    // 百分率座標で配置（リサイズ対応）
    hlDiv.style.left   = (hl.x / pageData.width * 100) + "%";
    hlDiv.style.top    = (hl.y / pageData.height * 100) + "%";
    hlDiv.style.width  = (hl.w / pageData.width * 100) + "%";
    hlDiv.style.height = (hl.h / pageData.height * 100) + "%";

    // カスタムプロパティで色設定
    hlDiv.style.setProperty("--hl-color", color.border);
    hlDiv.style.setProperty("--hl-bg", color.bg);
    hlDiv.style.setProperty("--hl-bg-hover", color.hover);

    // データ属性
    hlDiv.setAttribute("data-field-id", hl.field_id);
    hlDiv.setAttribute("data-label", hl.label);
    hlDiv.setAttribute("data-value", hl.value);
    hlDiv.setAttribute("data-cell", hl.cell || "");
    hlDiv.setAttribute("data-color", color.border);

    // ツールチップイベント
    hlDiv.addEventListener("mouseenter", _onHighlightEnter);
    hlDiv.addEventListener("mouseleave", _onHighlightLeave);

    pageContainer.appendChild(hlDiv);
  }

  wrap.appendChild(pageContainer);
  return wrap;
}


// ── ツールチップ ─────────────────────────────────────────────────────────────

function _onHighlightEnter(e) {
  var el = e.currentTarget;
  var label = el.getAttribute("data-label");
  var value = el.getAttribute("data-value");
  var cell  = el.getAttribute("data-cell");
  var color = el.getAttribute("data-color");

  _hideTooltip();

  var tooltip = document.createElement("div");
  tooltip.className = "review-tooltip";

  var html =
    '<div class="review-tooltip-header">' +
      '<span class="review-tooltip-dot" style="background:' + color + '"></span>' +
      '<span class="review-tooltip-label">' + _escapeHtml(label) + '</span>' +
    '</div>' +
    '<div class="review-tooltip-body">' +
      '<div class="review-tooltip-value">' + _escapeHtml(value) + '</div>' +
    '</div>';

  if (cell) {
    html +=
      '<div class="review-tooltip-footer">' +
        '<span class="review-tooltip-meta-label">Excel:</span>' +
        '<span class="review-tooltip-cell">' + _escapeHtml(cell) + '</span>' +
      '</div>';
  }

  tooltip.innerHTML = html;

  // ページコンテナに追加（position:relative な親）
  var pageContainer = el.closest(".review-page-container");
  if (pageContainer) {
    pageContainer.appendChild(tooltip);
  } else {
    document.body.appendChild(tooltip);
  }

  // 位置計算（ハイライトの上に表示、画面外なら下に）
  var elRect = el.getBoundingClientRect();
  var tipRect = tooltip.getBoundingClientRect();

  if (pageContainer) {
    var containerRect = pageContainer.getBoundingClientRect();
    var tipLeft = elRect.left - containerRect.left;
    var tipTop = elRect.top - containerRect.top - tipRect.height - 8;

    // 上にスペースがなければ下に配置
    if (tipTop < 0) {
      tipTop = elRect.bottom - containerRect.top + 8;
    }

    // 右端からはみ出さない
    if (tipLeft + tipRect.width > containerRect.width) {
      tipLeft = containerRect.width - tipRect.width - 8;
    }
    if (tipLeft < 0) tipLeft = 8;

    tooltip.style.left = tipLeft + "px";
    tooltip.style.top = tipTop + "px";
  }

  _activeTooltip = tooltip;
}

function _onHighlightLeave() {
  _hideTooltip();
}

function _hideTooltip() {
  if (_activeTooltip && _activeTooltip.parentNode) {
    _activeTooltip.parentNode.removeChild(_activeTooltip);
  }
  _activeTooltip = null;
}


// ── ユーティリティ ───────────────────────────────────────────────────────────

function _escapeHtml(str) {
  if (!str) return "";
  var div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}


// ── review ボタン表示制御 ────────────────────────────────────────────────────

/**
 * 抽出完了後に呼ばれ、PDF ファイルがあれば review ボタンを表示する。
 * api.js の _applyResult() 内から呼ばれる想定。
 * ただし既存コードを変更しないため、MutationObserver で state-complete の
 * active 状態を監視して自動トリガーする。
 */
function _checkShowReviewButton() {
  var btn = document.getElementById("review-btn");
  if (!btn) return;

  var files = _getReviewFiles();
  var hasFiles = files && files.length > 0;
  btn.style.display = hasFiles ? "inline-flex" : "none";
}

// state-complete が active になったら review ボタン表示チェック
(function() {
  var stateComplete = document.getElementById("state-complete");
  if (!stateComplete) return;

  var observer = new MutationObserver(function(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      if (mutations[i].attributeName === "class") {
        if (stateComplete.classList.contains("active")) {
          _checkShowReviewButton();
          // キャッシュクリア（新しい処理結果のため）
          _reviewCache = {};
        }
      }
    }
  });

  observer.observe(stateComplete, { attributes: true });
})();
