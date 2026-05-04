import { EditorView, basicSetup } from 'codemirror';
import { EditorState, StateField, StateEffect } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { keymap, Decoration, hoverTooltip } from '@codemirror/view';
import { indentWithTab, defaultKeymap } from '@codemirror/commands';
import { autocompletion } from '@codemirror/autocomplete';

const textarea = document.getElementById('body');
const project = document.body.dataset.project || '';
const filename = document.body.dataset.filename || '';

if (textarea) {
  const stateEl = document.getElementById('save-state');
  const savedAtEl = document.getElementById('saved-at');
  const form = document.getElementById('editor-form');
  const wordCountEl = document.querySelector('[data-live-wordcount]');
  let typewriterMode = false;

  // --- Helpers ---
  function setSaveState(s) {
    if (stateEl) stateEl.textContent = s;
  }

  function liveWordCount(text) {
    if (!wordCountEl) return;
    const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length;
    const latin = (text.match(/[A-Za-z0-9]+/g) || []).length;
    wordCountEl.textContent = (cjk + latin) + ' 字';
  }

  // --- T2.2: Autocomplete [[ ---
  const wikiLinkAutocomplete = (context) => {
    let word = context.matchBefore(/\[\[[^\]]*/);
    if (!word || (word.from == word.to && !context.explicit)) return null;
    
    return {
      from: word.from + 2, // skip [[
      options: async () => {
        const query = word.text.slice(2);
        try {
          const res = await fetch(`/api/entities?project=${project}&q=${encodeURIComponent(query)}`);
          const data = await res.json();
          return data.entities.map(e => ({
            label: e.name,
            detail: e.kind,
            type: "constant",
            apply: (view, completion, from, to) => {
                view.dispatch({
                    changes: {from: from - 2, to, insert: `[[${e.id}|${e.name}]]`},
                    selection: {anchor: from - 2 + `[[${e.id}|${e.name}]]`.length}
                });
            }
          }));
        } catch (err) {
          return [];
        }
      }
    };
  };

  // --- T2.3: Wiki Link Decorations ---
  const wikiLinkDecorator = StateField.define({
    create() { return Decoration.none },
    update(deco, tr) {
        if (!tr.docChanged && deco.size > 0) return deco;
        let builder = [];
        for (let i = 1; i <= tr.state.doc.lines; i++) {
            let line = tr.state.doc.line(i);
            let match;
            const re = /\[\[(ent_[^|\]]+)\|([^\]]+)\]\]|\[\[([^|\]#]+)(?:#([^\]]+))?\]\]/g;
            while (match = re.exec(line.text)) {
                let from = line.from + match.index;
                let to = from + match[0].length;
                let isID = !!match[1];
                builder.push(Decoration.mark({
                    class: isID ? "wiki-link-id" : "wiki-link-name"
                }).range(from, to));
            }
        }
        return Decoration.set(builder);
    },
    provide: f => EditorView.decorations.from(f)
  });

  // --- T2.3: Hover Tooltip ---
  const wikiLinkHover = hoverTooltip((view, pos, side) => {
    let {from, to, text} = view.state.doc.lineAt(pos);
    let start = pos, end = pos;
    while (start > from && text[start - from - 1] != "[" ) start--;
    while (end < to && text[end - from] != "]") end++;
    if (text.slice(start - from, start - from + 2) != "[[" || text.slice(end - from - 2, end - from) != "]]") return null;

    const raw = text.slice(start - from, end - from);
    return {
        pos: start,
        end: end,
        above: true,
        create(view) {
            let dom = document.createElement("div");
            dom.className = "cm-wiki-tooltip p-3 bg-panel border border-border_color rounded shadow-lg text-sm max-w-xs";
            dom.textContent = "Loading entity info...";
            
            // Extract ID or Name
            const match = /\[\[(ent_[^|\]]+)/.exec(raw);
            if (match) {
                fetch(`/api/entities/${match[1]}`)
                    .then(res => res.json())
                    .then(data => {
                        if (data.status === 'ok') {
                            const e = data.entity;
                            dom.innerHTML = `
                                <div class="font-bold text-accent mb-1">${e.name}</div>
                                <div class="text-xs text-muted mb-2">${e.kind}</div>
                                <div class="text-xs">${e.md_path ? 'Has notes' : 'No notes yet'}</div>
                                <div class="mt-2 text-xs flex gap-1">
                                    <a href="/projects/${project}/entities/${e.id}" class="text-accent hover:underline">View Detail</a>
                                </div>
                            `;
                        } else {
                            dom.textContent = "Entity not found";
                        }
                    });
            } else {
                dom.textContent = "Unbound name link. Click to bind.";
            }
            return {dom};
        }
    };
  });

  // --- T2.6: Scene Mode Logic ---
  const sceneListEl = document.getElementById('scene-editor-list');
  let sceneViews = [];

  function parseScenes(text) {
    const lines = text.split('\n');
    const scenes = [];
    let current = { title: 'Intro (No Header)', body: [] };
    
    lines.forEach(line => {
        if (line.startsWith('## ')) {
            if (current.body.length > 0 || current.title !== 'Intro (No Header)') {
                scenes.push({ ...current, body: current.body.join('\n') });
            }
            current = { title: line.replace('## ', '').trim(), body: [] };
        } else {
            current.body.push(line);
        }
    });
    scenes.push({ ...current, body: current.body.join('\n') });
    return scenes;
  }

  function renderScenes() {
    const text = view.state.doc.toString();
    const scenes = parseScenes(text);
    const container = sceneListEl.querySelector('div');
    container.innerHTML = '';
    sceneViews = [];

    scenes.forEach((s, idx) => {
        const block = document.createElement('div');
        block.className = "bg-panel border border-border_color rounded-xl shadow-sm overflow-hidden flex flex-col";
        block.innerHTML = `
            <div class="px-4 py-2 bg-bg/50 border-b border-border_color flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <span class="text-[10px] font-bold text-muted uppercase tracking-widest">Scene ${idx + 1}</span>
                    <input type="text" class="scene-title bg-transparent border-0 font-bold text-sm focus:outline-none" value="${s.title}">
                </div>
                <div class="flex items-center gap-2">
                    <button class="text-[10px] text-muted hover:text-accent p-1" data-action="split-here" title="在此处拆分场景">✂️</button>
                    <button class="text-[10px] text-muted hover:text-danger p-1" data-action="delete-scene">🗑️</button>
                </div>
            </div>
            <div class="scene-cm-container p-4 min-h-[100px]"></div>
        `;
        
        container.appendChild(block);
        
        const scView = new EditorView({
            state: EditorState.create({
                doc: s.body,
                extensions: [
                    basicSetup,
                    markdown(),
                    EditorView.lineWrapping,
                    EditorView.updateListener.of((update) => {
                        if (update.docChanged) syncScenesToFull();
                    }),
                    EditorView.theme({ "&": { fontSize: "16px" } })
                ]
            }),
            parent: block.querySelector('.scene-cm-container')
        });
        
        sceneViews.push({
            view: scView,
            titleInput: block.querySelector('.scene-title')
        });

        block.querySelector('.scene-title').oninput = () => syncScenesToFull();
    });
  }

  function syncScenesToFull() {
    let fullText = "";
    sceneViews.forEach((sv, i) => {
        if (i > 0 || sv.titleInput.value !== 'Intro (No Header)') {
            fullText += `## ${sv.titleInput.value}\n`;
        }
        fullText += sv.view.state.doc.toString() + "\n";
    });
    
    // Update main view
    view.dispatch({
        changes: {from: 0, to: view.state.doc.length, insert: fullText.trim()}
    });
    textarea.value = fullText.trim();
    setSaveState('未保存');
    form.dataset.dirty = 'true';
    scheduleSave();
    liveWordCount(fullText);
  }

  window.addEventListener('set-editor-mode', (e) => {
    if (e.detail === 'scene') {
        renderScenes();
    }
  });

  // --- Editor Initialization ---
  const startState = EditorState.create({
    doc: textarea.value,
    extensions: [
      basicSetup,
      markdown(),
      EditorView.lineWrapping,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          setSaveState('未保存');
          form.dataset.dirty = 'true';
          textarea.value = update.state.doc.toString();
          scheduleSave();
          liveWordCount(textarea.value);
        }
      }),
      autocompletion({ override: [wikiLinkAutocomplete] }),
      wikiLinkDecorator,
      wikiLinkHover,
      keymap.of([
        ...defaultKeymap,
        indentWithTab,
        { key: "Ctrl-s", run: () => { form.requestSubmit(); return true; } },
        { key: "Cmd-s", run: () => { form.requestSubmit(); return true; } }
      ]),
      // Theme matching
      EditorView.theme({
        "&": { height: "100%", fontSize: "18px" },
        ".cm-scroller": { overflow: "auto" },
        ".wiki-link-id": { color: "var(--accent)", textDecoration: "underline" },
        ".wiki-link-name": { color: "#f87171", textDecoration: "dotted underline" }
      })
    ]
  });

  const view = new EditorView({
    state: startState,
    parent: textarea.parentElement
  });
  textarea.style.display = 'none';

  // --- Autosave & HTMX logic (reused from old editor.js) ---
  let saveTimeout;
  function scheduleSave() {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
      if (form.dataset.dirty === 'true') {
        form.requestSubmit();
      }
    }, 5000);
  }

  form.addEventListener('submit', () => {
    setSaveState('保存中...');
  });

  document.body.addEventListener('htmx:beforeRequest', (evt) => {
    if (evt.target === form || evt.detail.elt?.closest?.('#editor-form')) {
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

  // typewriter mode
  document.body.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="typewriter"]');
    if (!btn) return;
    typewriterMode = !typewriterMode;
    window.NovelHubUI?.showToast(typewriterMode ? '打字机模式开启' : '打字机模式关闭');
  });

  // Initial count
  liveWordCount(textarea.value);
}

// Tag fields handling (unchanged logic, just ensuring it works in module)
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
        chip.innerHTML = `<span>${tag}</span><button type="button" class="hover:text-danger hover:bg-danger/10 rounded-full w-4 h-4 flex items-center justify-center transition-colors">&times;</button>`;
        chip.querySelector('button').addEventListener('click', () => {
          let currentTags = hiddenInput.value.split(',').map(t => t.trim()).filter(t => t);
          hiddenInput.value = currentTags.filter(t => t !== tag).join(',');
          renderChips();
          document.getElementById('editor-form').dataset.dirty = 'true';
        });
        chipsContainer.appendChild(chip);
      });
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        const tag = input.value.trim();
        if (tag) {
            let tags = hiddenInput.value.split(',').map(t => t.trim()).filter(t => t);
            if (!tags.includes(tag)) {
                tags.push(tag);
                hiddenInput.value = tags.join(',');
                renderChips();
                document.getElementById('editor-form').dataset.dirty = 'true';
            }
            input.value = '';
        }
      }
    });
    renderChips();
});
