// Stänger kontomenyn vid klick utanför eller Escape.
document.addEventListener('click', e => {
  document.querySelectorAll('details.account[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('details.account[open]').forEach(d => { d.open = false; d.querySelector('summary').focus(); });
});
