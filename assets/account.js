// Mitt konto: import av en JSON-export (läses i webbläsaren och skickas som JSON).
(() => {
  const input = document.querySelector('#importFile');
  if (!input) return;
  const status = document.querySelector('#importStatus');
  input.addEventListener('change', async () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > 4 * 1024 * 1024) { status.textContent = 'Filen är för stor (högst 4 MB).'; return; }
    status.textContent = 'Importerar…';
    try {
      const r = await fetch('/api/import', { method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': input.dataset.csrf }, body: await f.text() });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { status.textContent = j.error || 'Importen misslyckades.'; return; }
      status.textContent = 'Importerat: ' + Object.entries(j.imported).map(([k, v]) => `${v} ${k}`).join(', ') + '.';
    } catch { status.textContent = 'Importen misslyckades.'; }
  });
})();
