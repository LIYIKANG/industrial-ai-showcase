// 后端接口封装。所有请求都走这里，方便统一处理错误。

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let body;
  const text = await res.text();
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`响应不是合法 JSON（HTTP ${res.status}）：${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    throw new Error(body?.detail || body?.error || `请求失败 HTTP ${res.status}`);
  }
  return body;
}

const qs = (params) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') p.append(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  health:        ()      => request('/api/health'),
  params:        ()      => request('/api/params'),
  saveParams:    (body)  => request('/api/params', { method: 'PUT',  body: JSON.stringify(body) }),
  resetParams:   ()      => request('/api/params/reset', { method: 'POST' }),

  inventory:     (p)     => request(`/api/inventory${qs(p)}`),
  stats:         ()      => request('/api/inventory/stats'),
  reloadData:    ()      => request('/api/inventory/reload', { method: 'POST' }),
  bloomBands:    (p)     => request(`/api/inventory/bloom-bands${qs(p)}`),
  dashboard:     ()      => request('/api/dashboard'),

  compareMethods:()      => request('/api/compare/methods'),
  compare:       (body)  => request('/api/compare', { method: 'POST', body: JSON.stringify(body) }),
  pricing:       ()      => request('/api/pricing'),
  savePricing:   (body)  => request('/api/pricing', { method: 'PUT', body: JSON.stringify(body) }),
  resetPricing:  ()      => request('/api/pricing/reset', { method: 'POST' }),

  blend:         (body)  => request('/api/blend', { method: 'POST', body: JSON.stringify(body) }),
  backtest:      (p)     => request(`/api/backtest${qs(p)}`),
};
