(function () {
  function ensureSnapshotDiffPanel(snapshotList) {
    const metaPanel = snapshotList.closest('.flex.flex-col.gap-5') || snapshotList.parentElement;
    if (!metaPanel || document.getElementById('snapshot-diff-panel')) return;

    const panel = document.createElement('div');
    panel.id = 'snapshot-diff-panel';
    panel.className = 'hidden flex flex-col gap-2 pt-4 border-t border-border_color';
    panel.innerHTML = [
      '<div class="flex items-center justify-between">',
      '<h4 class="text-xs font-semibold text-muted uppercase">快照差异</h4>',
      '<button type="button" data-snapshot-diff-close class="text-[10px] text-muted hover:text-text_color">关闭</button>',
      '</div>',
      '<pre id="snapshot-diff-content" class="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-border_color bg-bg p-3 text-[11px] leading-relaxed font-mono text-muted">选择一个快照查看差异</pre>'
    ].join('');

    const dangerTitle = Array.from(metaPanel.querySelectorAll('h4'))
      .find((node) => node.textContent.trim() === '危险区');
    const dangerBlock = dangerTitle ? dangerTitle.closest('.flex.flex-col') : null;
    metaPanel.insertBefore(panel, dangerBlock || null);
  }

  function enhanceSnapshotDiff() {
    const snapshotTitle = Array.from(document.querySelectorAll('h4'))
      .find((node) => node.textContent.trim() === '快照历史');
    if (!snapshotTitle) return;

    const snapshotBlock = snapshotTitle.closest('.flex.flex-col');
    const snapshotList = snapshotBlock && snapshotBlock.querySelector('.max-h-48');
    if (!snapshotList) return;

    ensureSnapshotDiffPanel(snapshotList);

    snapshotList.querySelectorAll('button[onclick^="restoreSnapshot("]').forEach((restoreButton) => {
      if (restoreButton.parentElement && restoreButton.parentElement.querySelector('[data-snapshot-diff-id]')) return;
      const match = (restoreButton.getAttribute('onclick') || '').match(/restoreSnapshot\((\d+)\)/);
      if (!match) return;

      const wrapper = document.createElement('div');
      wrapper.className = 'flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity';

      const diffButton = document.createElement('button');
      diffButton.type = 'button';
      diffButton.dataset.snapshotDiffId = match[1];
      diffButton.className = 'text-[10px] text-muted hover:text-accent';
      diffButton.textContent = '对比';

      restoreButton.className = 'text-[10px] text-accent';
      restoreButton.classList.remove('opacity-0', 'group-hover:opacity-100', 'transition-opacity');
      restoreButton.parentElement.replaceChild(wrapper, restoreButton);
      wrapper.append(diffButton, restoreButton);
    });
  }

  async function showSnapshotDiff(id) {
    const panel = document.getElementById('snapshot-diff-panel');
    const content = document.getElementById('snapshot-diff-content');
    if (!panel || !content) return;
    panel.classList.remove('hidden');
    content.textContent = '正在加载差异...';
    try {
      const res = await fetch('/api/snapshots/' + id + '/diff');
      const data = await res.json();
      if (!res.ok || data.status !== 'ok') throw new Error(data.detail || '加载失败');
      content.textContent = data.diff || '该快照与当前内容没有差异。';
    } catch (err) {
      content.textContent = '加载失败：' + (err.message || err);
    }
  }

  document.addEventListener('click', (event) => {
    const diffButton = event.target.closest('[data-snapshot-diff-id]');
    if (diffButton) showSnapshotDiff(diffButton.dataset.snapshotDiffId);
    if (event.target.closest('[data-snapshot-diff-close]')) {
      const panel = document.getElementById('snapshot-diff-panel');
      if (panel) panel.classList.add('hidden');
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enhanceSnapshotDiff);
  } else {
    enhanceSnapshotDiff();
  }

  window.NovelHubSnapshotDiff = { enhance: enhanceSnapshotDiff };
})();
