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
})();
