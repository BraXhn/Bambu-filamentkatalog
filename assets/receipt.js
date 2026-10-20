// Kvitton i köphistoriken: bilder förminskas i webbläsaren (max 1800 px, JPEG) innan de laddas upp; PDF skickas som den är.
(() => {
  const csrf = () => (document.querySelector('input[name=csrf]') || {}).value || '';
  async function shrink(file) {
    if (!file.type.startsWith('image/')) return file;
    const img = await createImageBitmap(file).catch(() => null);
    if (!img) return file;
    const scale = Math.min(1, 1800 / Math.max(img.width, img.height));
    const c = document.createElement('canvas');
    c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    return new Promise(res => c.toBlob(b => res(b || file), 'image/jpeg', 0.82));
  }
  document.addEventListener('change', async e => {
    const input = e.target.closest('input[data-receipt]');
    if (!input || !input.files[0]) return;
    const status = input.closest('td').querySelector('.receipt-status');
    const say = t => { if (status) status.textContent = ' ' + t; };
    say('Laddar upp…');
    try {
      const blob = await shrink(input.files[0]);
      if (blob.size > 3 * 1024 * 1024) { say('Filen är för stor (högst 3 MB).'); return; }
      const r = await fetch('/api/receipt/' + input.dataset.receipt, { method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': blob.type || 'application/octet-stream', 'X-CSRF-Token': csrf() }, body: blob });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { say(j.error || 'Kunde inte spara kvittot.'); return; }
      location.reload();
    } catch { say('Kunde inte spara kvittot.'); }
  });
})();
