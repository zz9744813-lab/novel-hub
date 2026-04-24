const textarea = document.getElementById('body');
if (textarea && window.CodeMirror) {
  const editor = CodeMirror.fromTextArea(textarea, {
    mode: 'markdown',
    lineNumbers: true,
    lineWrapping: true,
  });

  const form = document.getElementById('editor-form');
  form.addEventListener('submit', () => {
    editor.save();
  });

  document.body.addEventListener('htmx:beforeRequest', () => {
    editor.save();
  });
}
