/**
 * static/recent.js
 * ================
 * 最近处理记录：基于 localStorage 的轻量持久化。
 *
 * 公开函数：
 *   saveRecentFile(name, size) → 追加一条记录并刷新列表（最多保留 6 条）
 *   renderRecent()             → 从 localStorage 读取并渲染最近记录列表
 *
 * 依赖（全局 var）：
 *   i18n.js  → t()
 *   utils.js → formatFileSize(), timeAgo()
 */

var RECENT_STORAGE_KEY = "pdf_recent";
var RECENT_MAX         = 6;

/** ユーザー別にストレージキーを設定する（ログイン後に呼び出す） */
function setRecentUser(username) {
  RECENT_STORAGE_KEY = "pdf_recent_" + username;
  renderRecent();
}

/** 保存一条最近记录并刷新 UI */
function saveRecentFile(name, size) {
  try {
    var recent = JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY) || "[]");
    recent.unshift({ name: name, size: size, time: Date.now() });
    recent = recent.slice(0, RECENT_MAX);
    localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(recent));
    renderRecent();
  } catch (e) {
    // localStorage 不可用时静默忽略
  }
}

/** 渲染最近处理记录列表（无记录时隐藏整个区块） */
function renderRecent() {
  try {
    var recent = JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY) || "[]");
    var section = document.getElementById("recent-section");
    var list    = document.getElementById("recent-list");
    if (!recent.length) {
      section.style.display = "none";
      return;
    }
    section.style.display = "block";
    list.innerHTML = recent.map(function(f) {
      return (
        '<div class="recent-item">' +
          '<div class="recent-item-icon">' +
            '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#EF4444" stroke-width="2">' +
              '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>' +
              '<path d="M14 2v6h6"/>' +
            "</svg>" +
          "</div>" +
          '<div class="recent-item-info">' +
            '<div class="recent-item-name">' + f.name + "</div>" +
            '<div class="recent-item-meta">' + formatFileSize(f.size) + " &bull; " + timeAgo(f.time) + "</div>" +
          "</div>" +
        "</div>"
      );
    }).join("");
  } catch (e) {
    // 解析失败时静默忽略
  }
}
