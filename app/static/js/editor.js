(function () {
  const textarea = document.getElementById('body');
  if (!textarea || !window.CodeMirror) return;

  const stateEl = document.getElementById('save-state');
  const savedAtEl = document.getElementById('saved-at');
  const form = document.getElementById('editor-form');
  const titleInput = document.querySelector('.chapter-title');
  const titleMirror = document.querySelector('[data-title-mirror]');
  const fontPicker = document.getElementById('font-size-picker');
  let typewriterMode = false;

  const editor = CodeMirror.fromTextArea(textarea, {
    mode: 'markdown',
    lineNumbers: false,
    lineWrapping: true,
    viewportMargin: Infinity,
  });

  function setSaveState(s) {
    if (stateEl) stateEl.textContent = s;
  }

  editor.on('change', () => {
    setSaveState('未保存');
    form.dataset.dirty = 'true';
  });

  document.body.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="typewriter"]');
    if (!btn) return;
    typewriterMode = !typewriterMode;
    window.NovelHubUI?.showToast(typewriterMode ? '打字机模式开启' : '打字机模式关闭');
  });

  editor.on('cursorActivity', () => {
    if (!typewriterMode) return;
    const scrollInfo = editor.getScrollInfo();
    const cursor = editor.cursorCoords(null, 'local');
    const targetY = cursor.top - scrollInfo.clientHeight / 2;
    editor.scrollTo(null, Math.max(0, targetY));
  });

  if (titleInput && titleMirror) {
    titleInput.addEventListener('input', () => {
      titleMirror.value = titleInput.value;
    });
  }

  if (fontPicker) {
    fontPicker.addEventListener('change', () => {
      const wrap = editor.getWrapperElement();
      wrap.style.fontSize = fontPicker.value === 'small' ? '16px' : fontPicker.value === 'large' ? '21px' : '18px';
    });
  }

  form.addEventListener('submit', () => {
    editor.save();
    setSaveState('保存中...');
  });

  document.body.addEventListener('htmx:beforeRequest', (evt) => {
    if (evt.target === form || evt.detail.elt?.closest?.('#editor-form')) {
      editor.save();
      setSaveState('保存中...');
    }
  });

  document.body.addEventListener('htmx:afterSwap', (evt) => {
    if (evt.target.id === 'save-result') {
      const ok = evt.target.querySelector('.save-ok');
      if (ok) {
        setSaveState('已保存');
        form.dataset.dirty = 'false';
        const savedAt = ok.dataset.savedAt;
        if (savedAtEl) savedAtEl.textContent = savedAt;
        window.NovelHubUI?.showToast('保存成功');
      }
    }
  });

  setInterval(() => {
    if (form.dataset.dirty === 'true') {
      form.requestSubmit();
    }
  }, 60000);
})();
