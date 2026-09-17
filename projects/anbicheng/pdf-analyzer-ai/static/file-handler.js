/**
 * static/file-handler.js
 * ======================
 * 文件选择与管理：校验、去重、列表渲染、重置。
 *
 * 二段アップロード対応：契約書（contract）と精算書（settlement）を
 * それぞれ独立した槽位（slot）として管理する。両槽位のファイルを
 * 合算したものが app.js の currentFiles（api.js が送信に使用）。
 *
 * 公开函数：
 *   handleFilesForSlot(slotKey, fileList)  → 指定槽位に有効ファイルを追加し UI 更新
 *   updateSlotUI(slotKey)                  → 指定槽位のドロップゾーン・ファイル列表を更新
 *   rebuildCurrentFiles()                  → 両槽位を合算して currentFiles を再構築
 *   resetFileUI()                          → 全槽位のファイルをクリアし UI をリセット
 *
 * 依赖（全局 var）：
 *   i18n.js   → t(), currentLang
 *   utils.js  → formatFileSize(), escapeHtml()
 *   app.js    → currentFiles, uploadBtn
 */

var ALLOWED_EXTS = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"];

// 槽位状态：各槽位が自前の files 配列と DOM 要素 ID を保持する
var uploadSlots = {
  contract: {
    files: [],
    dropId: "drop-area-contract",
    inputId: "file-input-contract",
    browseId: "browse-btn-contract",
    listId: "selected-files-list-contract",
  },
  settlement: {
    files: [],
    dropId: "drop-area-settlement",
    inputId: "file-input-settlement",
    browseId: "browse-btn-settlement",
    listId: "selected-files-list-settlement",
  },
  general: {
    files: [],
    dropId: "drop-area-general",
    inputId: "file-input-general",
    browseId: "browse-btn-general",
    listId: "selected-files-list-general",
  },
  // 旧来の単一アップロード（精算書・請求書ページが使用）。該当 DOM が無いページでは無視される。
  legacy: {
    files: [],
    dropId: "drop-area",
    inputId: "file-input",
    browseId: "browse-btn",
    listId: "selected-files-list",
  },
};

/**
 * 指定槽位に有効ファイルを追加（去重）し、UI を更新する。
 * @param {string} slotKey "contract" | "settlement"
 * @param {FileList|File[]} files
 */
function handleFilesForSlot(slotKey, files) {
  var slot = uploadSlots[slotKey];
  if (!slot) return;

  var validFiles = Array.from(files).filter(function (f) {
    var ext = f.name.toLowerCase().substring(f.name.lastIndexOf("."));
    return ALLOWED_EXTS.includes(ext);
  });
  if (validFiles.length === 0) {
    alert(t("alert_pdf"));
    return;
  }
  // 按文件名去重
  var existingNames = new Set(slot.files.map(function (f) { return f.name; }));
  validFiles.forEach(function (f) {
    if (!existingNames.has(f.name)) slot.files.push(f);
  });

  updateSlotUI(slotKey);
  rebuildCurrentFiles();
}

/** 指定槽位のドロップゾーン文字とファイル列表を更新 */
function updateSlotUI(slotKey) {
  var slot = uploadSlots[slotKey];
  if (!slot) return;

  var dropArea = document.getElementById(slot.dropId);
  if (!dropArea) return;
  var titleEl = dropArea.querySelector(".drop-zone-title");
  var hintEl = dropArea.querySelector(".drop-zone-hint");

  if (slot.files.length === 0) {
    dropArea.classList.remove("has-file");
    if (titleEl) titleEl.textContent = t(titleEl.dataset.i18n);
    if (hintEl) hintEl.textContent = t(hintEl.dataset.i18n);
  } else {
    dropArea.classList.add("has-file");
    var totalSize = slot.files.reduce(function (s, f) { return s + f.size; }, 0);
    if (slot.files.length === 1) {
      if (titleEl) titleEl.textContent = slot.files[0].name;
      if (hintEl) hintEl.textContent = formatFileSize(slot.files[0].size) + " - " + t("file_selected");
    } else {
      if (titleEl) titleEl.textContent = slot.files.length + t("files_selected_suffix");
      if (hintEl) hintEl.textContent = formatFileSize(totalSize) + " - " + t("file_selected");
    }
  }

  // 渲染多文件列表（单文件不显示）
  var listContainer = document.getElementById(slot.listId);
  if (listContainer && slot.files.length > 1) {
    listContainer.style.display = "block";
    listContainer.innerHTML = slot.files.map(function (f, i) {
      return (
        '<div class="selected-file-item">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#EF4444" stroke-width="2">' +
            '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>' +
            '<path d="M14 2v6h6"/>' +
          "</svg>" +
          '<span class="selected-file-name">' + escapeHtml(f.name) + "</span>" +
          '<span class="selected-file-size">' + formatFileSize(f.size) + "</span>" +
          '<button class="selected-file-remove" data-index="' + i + '" title="Remove">&times;</button>' +
        "</div>"
      );
    }).join("");

    // 绑定删除按钮
    listContainer.querySelectorAll(".selected-file-remove").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        slot.files.splice(parseInt(btn.dataset.index), 1);
        updateSlotUI(slotKey);
        rebuildCurrentFiles();
      });
    });
  } else if (listContainer) {
    listContainer.innerHTML = "";
    listContainer.style.display = "none";
  }
}

/** 全槽位のファイルを合算して currentFiles を再構築し、解析ボタンの活性を更新 */
function rebuildCurrentFiles() {
  currentFiles = Object.keys(uploadSlots).reduce(function (acc, key) {
    return acc.concat(uploadSlots[key].files);
  }, []);
  uploadBtn.disabled = currentFiles.length === 0;
}

/**
 * currentFiles と同じ並びで各ファイルの槽位キー配列を返す。
 * バックエンドの mode 判定（契約書のみ等）に使用する。
 * rebuildCurrentFiles と同一の走査順なので currentFiles と整列が保証される。
 */
function getFileSlots() {
  return Object.keys(uploadSlots).reduce(function (acc, key) {
    return acc.concat(uploadSlots[key].files.map(function () { return key; }));
  }, []);
}

/** 全槽位のファイルをクリアし、各ドロップゾーンを初期状態に戻す */
function resetFileUI() {
  Object.keys(uploadSlots).forEach(function (slotKey) {
    var slot = uploadSlots[slotKey];
    slot.files = [];
    var input = document.getElementById(slot.inputId);
    if (input) input.value = "";
    updateSlotUI(slotKey);
  });
  currentFiles = [];
  uploadBtn.disabled = true;
}
