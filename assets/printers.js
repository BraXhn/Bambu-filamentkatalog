// Skrivare: uppdatera statuskorten var 5:e sekund medan sidan är synlig (öppna inställningar lämnas orörda).
(() => {
  const box = document.querySelector('#printerCards');
  if (!box) return;
  async function tick() {
    if (!document.hidden && !box.querySelector('details.settings[open]') && !box.contains(document.activeElement)) {
      try {
        const r = await fetch('/skrivare/kort', { credentials: 'same-origin' });
        if (r.ok) box.innerHTML = await r.text();
      } catch {}
    }
    setTimeout(tick, 5000);
  }
  setTimeout(tick, 5000);
  // Kamerabilder: visas först när en bild har laddats (ingen trasig ikon om skrivaren inte svarar), uppdateras var 10:e sekund.
  const show = i => { if (i.complete && i.naturalWidth) i.hidden = false; };
  document.addEventListener('load', e => { const i = e.target; if (i instanceof HTMLImageElement && i.dataset.cam) i.hidden = false; }, true);
  document.addEventListener('error', e => { const i = e.target; if (i instanceof HTMLImageElement && i.dataset.cam) i.hidden = true; }, true);
  document.querySelectorAll('img[data-cam]').forEach(show);
  setInterval(() => { if (document.hidden) return; document.querySelectorAll('img[data-cam]').forEach(i => { i.src = `/skrivare/${i.dataset.cam}/bild.jpg?t=${Date.now()}`; }); }, 10000);
})();
