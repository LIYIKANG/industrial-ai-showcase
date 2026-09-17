// 通用 UI 工具：DOM 构造、数字格式化、提示条。

/** 创建元素。children 可以是字符串、节点或数组。 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** 数字格式化：null 显示为 —，否则保留指定小数并加千分位。 */
export function fmt(v, decimals = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (typeof v !== 'number') return String(v);
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** 自动小数位：整数不显示小数，否则最多 3 位。 */
export function fmtAuto(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v !== 'number') return String(v);
  return Number.isInteger(v) ? v.toLocaleString('zh-CN') : fmt(v, 3).replace(/0+$/, '').replace(/\.$/, '');
}

let toastTimer = null;
export function toast(msg, isError = false) {
  const node = $('#toast');
  node.textContent = msg;
  node.classList.toggle('err', isError);
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), isError ? 5000 : 2600);
}

/** 空状态占位。 */
export function empty(text) {
  return el('div', { class: 'empty', text });
}
