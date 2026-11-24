// Skrivare: uppdatera statuskorten var 5:e sekund medan sidan är synlig (öppna inställningar lämnas orörda).
(() => {
  const box = document.querySelector('#printerCards');
  if (!box) return;
  async function tick() {
    if (!document.hidden && !box.querySelector('details[open]') && !box.contains(document.activeElement)) {
      try {
        const r = await fetch('/skrivare/kort', { credentials: 'same-origin' });
        if (r.ok) box.innerHTML = await r.text();
      } catch {}
    }
    setTimeout(tick, 5000);
  }
  setTimeout(tick, 5000);
  // Kamerabilder: dölj om skrivaren inte svarar, uppdatera var 10:e sekund.
  document.addEventListener('error', e => { const i = e.target; if (i instanceof HTMLImageElement && i.dataset.cam) i.hidden = true; }, true);
  setInterval(() => { if (document.hidden) return; document.querySelectorAll('img[data-cam]').forEach(i => { if (!i.hidden) i.src = `/skrivare/${i.dataset.cam}/bild.jpg?t=${Date.now()}`; }); }, 10000);
})();
