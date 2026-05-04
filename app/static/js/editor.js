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

  let saveTimeout;
  
  function scheduleSave() {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
      if (form.dataset.dirty === 'true') {
        form.requestSubmit();
      }
    }, 5000);
  }

  editor.on('change', () => {
    setSaveState('未保存');
    form.dataset.dirty = 'true';
    scheduleSave();
  });

  window.addEventListener('blur', () => {
    if (form.dataset.dirty === 'true') form.requestSubmit();
  });
  
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && form.dataset.dirty === 'true') {
      form.requestSubmit();
    }
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
      const error = evt.target.querySelector('.save-error');
      if (error) {
        setSaveState('保存冲突');
        window.NovelHubUI?.showToast(error.textContent || '保存失败');
        return;
      }
      const ok = evt.target.querySelector('.save-ok');
      if (ok) {
        setSaveState('已保存');
        form.dataset.dirty = 'false';
        const savedAt = ok.dataset.savedAt;
        const newMtime = ok.dataset.newMtime;
        if (savedAtEl) savedAtEl.textContent = savedAt;
        if (newMtime) {
          const mtimeInput = document.querySelector('input[name="_loaded_mtime"]');
          if (mtimeInput) mtimeInput.value = newMtime;
        }
        window.NovelHubUI?.showToast('保存成功');
      }
    }
  });

  setInterval(() => {
    if (form.dataset.dirty === 'true') {
      form.requestSubmit();
    }
  }, 60000);

  // Tag fields handling
  document.querySelectorAll('[data-tag-field]').forEach(field => {
    const input = field.querySelector('[data-input]');
    const chipsContainer = field.querySelector('.chips');
    const targetName = field.dataset.target;
    const hiddenInput = document.querySelector(`[data-hidden="${targetName}"]`);

    if (!input || !chipsContainer || !hiddenInput) return;

    function renderChips() {
      const tags = hiddenInput.value.split(',').map(t => t.trim()).filter(t => t);
      chipsContainer.innerHTML = '';

      tags.forEach(tag => {
        const chip = document.createElement('div');
        chip.className = 'inline-flex items-center gap-1 bg-accent/10 border border-accent/20 text-accent rounded-full px-2 py-0.5 text-xs';
        chip.innerHTML = `
          <span>${tag}</span>
          <button type="button" class="hover:text-danger hover:bg-danger/10 rounded-full w-4 h-4 flex items-center justify-center transition-colors">&times;</button>
        `;
        chip.querySelector('button').addEventListener('click', () => {
          removeTag(tag);
        });
        chipsContainer.appendChild(chip);
      });
    }

    function addTag(tag) {
      tag = tag.trim();
      if (!tag) return;

      let tags = hiddenInput.value.split(',').map(t => t.trim()).filter(t => t);
      if (!tags.includes(tag)) {
        tags.push(tag);
        hiddenInput.value = tags.join(',');
        renderChips();
        form.dataset.dirty = 'true';
        setSaveState('未保存');
      }
      input.value = '';
    }

    function removeTag(tagToRemove) {
      let tags = hiddenInput.value.split(',').map(t => t.trim()).filter(t => t);
      tags = tags.filter(t => t !== tagToRemove);
      hiddenInput.value = tags.join(',');
      renderChips();
      form.dataset.dirty = 'true';
      setSaveState('未保存');
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        addTag(input.value);
      }
    });

    // Initial render
    renderChips();
  });
})();
