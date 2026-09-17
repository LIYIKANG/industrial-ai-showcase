/**
 * static/auth.js
 * ==============
 * 認証ユーティリティ。必ず他のスクリプトより先にロードすること。
 *
 * 機能：
 *   - Access Token を sessionStorage で管理（タブを閉じると自動クリア）
 *   - window.fetch をラップして全 API コールに Authorization ヘッダーを自動付与
 *   - 401 時に /auth/refresh を試み、失敗したらログインページへリダイレクト
 *   - window.Auth.requireAuth() で保護ページの認証チェック
 */

(function () {
  'use strict';

  var TOKEN_KEY = 'access_token';

  // ── Token 管理 ─────────────────────────────────────────────────────────────

  window.Auth = {
    getToken: function () {
      return sessionStorage.getItem(TOKEN_KEY);
    },

    setToken: function (token) {
      sessionStorage.setItem(TOKEN_KEY, token);
    },

    clearToken: function () {
      sessionStorage.removeItem(TOKEN_KEY);
    },

    /** Access Token をリフレッシュ（Cookie の Refresh Token を使用）。
     *  成功時：新しい token を返す。失敗時：null を返す。 */
    refreshToken: async function () {
      try {
        var resp = await window._rawFetch('/auth/refresh', { method: 'POST' });
        if (resp.ok) {
          var data = await resp.json();
          window.Auth.setToken(data.access_token);
          return data.access_token;
        }
      } catch (_) {}
      return null;
    },

    /** 保護ページで呼び出す：未ログインならログインページへリダイレクト。 */
    requireAuth: function () {
      if (!window.Auth.getToken()) {
        window.location.href = '/showcase';
      }
    },

    /** ログアウト処理（サーバーに通知 → token クリア → ログインページへ）。 */
    logout: async function () {
      try {
        await window._rawFetch('/auth/logout', {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + window.Auth.getToken() },
        });
      } catch (_) {}
      window.Auth.clearToken();
      window.location.href = '/showcase';
    },

    /** 現在のユーザー情報を取得（表示名・ロール）。 */
    fetchMe: async function () {
      try {
        var resp = await fetch('/auth/me');
        if (resp.ok) return await resp.json();
      } catch (_) {}
      return null;
    },
  };

  // ── fetch ラッパー ─────────────────────────────────────────────────────────
  // オリジナルの fetch を保持（リフレッシュ時にインターセプトループを回避）

  window._rawFetch = window.fetch.bind(window);

  window.fetch = async function (url, options) {
    options = options || {};

    // Authorization ヘッダーを自動付与（既に設定済みの場合はスキップ）
    var token = window.Auth.getToken();
    if (token && !(options.headers && options.headers['Authorization'])) {
      options.headers = Object.assign({}, options.headers, {
        Authorization: 'Bearer ' + token,
      });
    }

    var response = await window._rawFetch(url, options);

    // 401 かつリトライ未実施 → リフレッシュを試みる
    if (response.status === 401 && !options._authRetry) {
      var newToken = await window.Auth.refreshToken();
      if (newToken) {
        options._authRetry = true;
        options.headers = Object.assign({}, options.headers, {
          Authorization: 'Bearer ' + newToken,
        });
        return window._rawFetch(url, options);
      } else {
        // リフレッシュ失敗 → ログインページへ
        window.Auth.clearToken();
        window.location.href = '/showcase';
        return response;
      }
    }

    return response;
  };
})();
