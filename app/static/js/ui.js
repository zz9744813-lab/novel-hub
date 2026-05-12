(function () {
  const root = document.documentElement;
  const body = document.body;
  const toast = document.getElementById('toast');

  // We use Alpine.js for theme now, but keep this for backward compatibility if needed
  const storedTheme = localStorage.getItem('theme');
  if (storedTheme) root.setAttribute('data-theme', storedTheme);

  function readCookie(name) {
    return document.cookie
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(name + '='))
      ?.slice(name.length + 1) || '';
  }

  window.getCSRFToken = function () {
    const row = document.cookie
      .split('; ')
      .find((row) => row.startsWith('csrftoken='));
    return row ? decodeURIComponent(row.split('=').slice(1).join('=')) : '';
  };

  window.csrfHeaders = function (extra = {}) {
    const token = window.getCSRFToken();
    return token ? { ...extra, 'X-CSRFToken': token } : { ...extra };
  };

  window.csrfFetch = function (url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    if (!['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)) {
      const token = window.getCSRFToken();
      if (token && !headers.has('X-CSRFToken') && !headers.has('x-csrftoken')) {
        headers.set('X-CSRFToken', token);
      }
    }
    return fetch(url, { ...options, headers });
  };

  document.addEventListener('htmx:configRequest', function (event) {
    const token = window.getCSRFToken();
    if (token) event.detail.headers['X-CSRFToken'] = token;
  });

  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1800);
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;

    // Theme action is handled by Alpine.js in base.html
    if (action === 'focus') {
      body.dataset.focus = body.dataset.focus === 'on' ? 'off' : 'on';
      showToast(body.dataset.focus === 'on' ? '专注模式已开启' : '专注模式已关闭');
    }
    if (action === 'toggle-left') body.dataset.hideLeft = body.dataset.hideLeft === 'on' ? 'off' : 'on';
    if (action === 'toggle-right') body.dataset.hideRight = body.dataset.hideRight === 'on' ? 'off' : 'on';
  });

  window.NovelHubUI = { showToast };
})();
