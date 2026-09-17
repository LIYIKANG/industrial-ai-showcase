/**
 * static/utils.js
 * ===============
 * 通用工具函数（无副作用，不依赖全局状态）。
 *
 * 公开函数：
 *   formatFileSize(bytes)  → "1.2 MB"
 *   getTimeStr()           → "14:32:01"
 *   getNowStr()            → "2026年3月17日 14:32"
 *   escapeHtml(str)        → HTML 转义字符串
 *   sleep(ms)              → Promise，用于 async/await 延迟
 *   timeAgo(ts)            → "3分前" / "刚刚"
 *   preventDefaults(e)     → e.preventDefault() + e.stopPropagation()
 *
 * 依赖：i18n.js（t, currentLang）
 */

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function getTimeStr() {
  return new Date().toLocaleTimeString(
    currentLang === "ja" ? "ja-JP" : "zh-CN",
    { hour12: false }
  );
}

function getNowStr() {
  return new Date().toLocaleDateString(
    currentLang === "ja" ? "ja-JP" : "zh-CN",
    { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
  );
}

/** 安全转义 HTML 特殊字符（防 XSS） */
function escapeHtml(str) {
  var div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/** Promise 延迟（用于视觉反馈） */
function sleep(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}

/** 相对时间描述（依赖 i18n t()） */
function timeAgo(ts) {
  var diff = Date.now() - ts;
  var mins = Math.floor(diff / 60000);
  if (mins < 1) return t("time_just_now");
  if (mins < 60) return mins + t("time_minutes_ago");
  var hours = Math.floor(mins / 60);
  if (hours < 24) return hours + t("time_hours_ago");
  return Math.floor(hours / 24) + t("time_days_ago");
}

/** 阻止事件冒泡和默认行为（用于拖拽区域） */
function preventDefaults(e) {
  e.preventDefault();
  e.stopPropagation();
}
