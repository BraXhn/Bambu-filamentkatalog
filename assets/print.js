// Utskriftsknappar på serverrenderade sidor (inga inline-skript: strikt Content-Security-Policy).
document.addEventListener('click',e=>{if(e.target.closest('[data-print]'))window.print()});
