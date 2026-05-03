(function () {
  const root = document.documentElement;
  const body = document.body;
  const toast = document.getElementById('toast');

  // We use Alpine.js for theme now, but keep this for backward compatibility if needed
  const storedTheme = localStorage.getItem('theme');
  if (storedTheme) root.setAttribute('data-theme', storedTheme);

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
