let data=[],shown=[],family='',table=false,swatches=false,onlyOwned=false;
const storageKey='bambu-owned-v1';
let owned=new Set();
try { const saved=JSON.parse(localStorage.getItem(storageKey)||'[]'); if(Array.isArray(saved)) owned=new Set(saved.filter(x=>typeof x==='string')); } catch {}
const ownedKey=d=>d.type+'|'+(d.code||d.name);
let bambuOwned={},bambuInfo=null;
const inBambu=d=>bambuOwned[ownedKey(d)];
const has=d=>owned.has(ownedKey(d))||!!inBambu(d);
const safeUrl=u=>/^https:\/\//i.test(String(u||''))?u:'#';const hexOk=h=>/^#[0-9A-Fa-f]{3,8}$/.test(h);
const $=s=>document.querySelector(s);const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function ownership(d){const b=inBambu(d);if(b){const left=b.grams?` · ${b.grams} g kvar`:'';return `<label class="ownership is-owned from-bambu" title="Synkat från ditt Bambu Lab-filamentbibliotek. Ändras i Bambu Handy eller Bambu Studio."><input type="checkbox" checked disabled aria-label="${esc(d.type+' '+d.name)} finns i ditt Bambu-bibliotek"><span>Bambu-biblioteket${b.spools>1?` · ${b.spools} spolar`:''}${esc(left)}</span></label>`}return `<label class="ownership ${owned.has(ownedKey(d))?'is-owned':''}"><input type="checkbox" data-owned="${esc(ownedKey(d))}" aria-label="Jag har ${esc(d.type+' '+d.name)}" ${owned.has(ownedKey(d))?'checked':''}><span>Jag har detta filament</span></label>`}
function color(d){const hx=d.hex.filter(hexOk);return hx.length>1?`linear-gradient(120deg,${hx.join(',')})`:hx[0]||'repeating-linear-gradient(45deg,#e5e9e2 0 8px,#f4f6f0 8px 16px)'}
function art(d,mini=false){if(!swatches&&d.image)return `<img loading="lazy" src="${esc(d.image)}${d.image.includes('?')?'&':'?'}width=${mini?120:500}" alt="${esc(d.type+' – '+d.name)}" data-fallback><span hidden>Bild kunde inte hämtas</span>`;return `<span role="img" aria-label="${esc(d.hex.length?'Färgprov '+d.name:'HEX-kod saknas')}" class="${mini?'mini':'swatch'}" style="background:${color(d)}">${!d.hex.length&&!mini?'<span style="position:absolute;bottom:20px;left:15px;font-size:12px;background:white;padding:6px">HEX-kod saknas</span>':''}</span>`}
function render(){let q=$('#search').value.trim().toLowerCase();shown=data.filter(d=>(!window.onlyDeals||window.priceInfo?.(ownedKey(d))?.deal)&&(!window.onlyPriced||window.catalogOffers?.some(o=>o.key===ownedKey(d)&&(!o.endAt||Date.parse(o.endAt)>Date.now())))&&(!onlyOwned||has(d))&&(!family||d.family===family)&&(!$('#type').value||d.type===$('#type').value)&&[d.type,d.name,d.code,...d.hex].join(' ').toLowerCase().includes(q));let key=$('#sort').value;const pi=d=>window.priceInfo?.(ownedKey(d));shown.sort(key==='price'?(a,b)=>(pi(a)?.best?.perKg??Infinity)-(pi(b)?.best?.perKg??Infinity)||a.name.localeCompare(b.name):key==='discount'?(a,b)=>(pi(b)?.deal?.o.discount??-1)-(pi(a)?.deal?.o.discount??-1)||(pi(a)?.best?.perKg??Infinity)-(pi(b)?.best?.perKg??Infinity):(a,b)=>(a[key]||'zzz').localeCompare(b[key]||'zzz','sv')||a.name.localeCompare(b.name));$('#count').textContent=`Visar ${shown.length} av ${data.length} färgvarianter · Du har ${data.filter(has).length}`;$('#ownedFilter').textContent=`Mina filament (${data.filter(has).length})`;$('#ownedFilter').classList.toggle('active',onlyOwned);$('#ownedFilter').setAttribute('aria-pressed',onlyOwned);$('#families').innerHTML=['',...new Set(data.map(d=>d.family))].map(f=>`<button class="${f===family?'active':''}" data-family="${esc(f)}" aria-pressed="${f===family}">${esc(f||'Alla material')}</button>`).join('');$('#results').className=table?'tablebox':'grid';if(!shown.length){$('#results').innerHTML='<div class="empty"><h2>Inga färger matchar din sökning</h2><p>Prova ett annat färgnamn eller rensa filtren.</p></div>';return}$('#results').innerHTML=table?`<table><thead><tr><th>Bild</th><th>Filamenttyp</th><th>Färg</th><th>Färgnummer</th><th>HEX</th><th>Lägsta pris</th><th>Priser</th><th>Jag har</th><th>Källa</th></tr></thead><tbody>${shown.map(d=>`<tr><td>${art(d,true)}</td><td>${esc(d.type)}</td><td>${esc(d.name)}</td><td>${esc(d.code||'Ej angivet')}</td><td>${esc(d.hex.join(' / '))||'Ej angiven'}</td><td>${(()=>{const e=window.priceInfo?.(ownedKey(d));return (e?.best?money(e.best.total):'—')+(e?.deal?` <span class="deal-badge">−${e.deal.o.discount} %</span>`:'')})()}</td><td><button data-prices="${esc(ownedKey(d))}">Jämför priser ↗</button></td><td>${ownership(d)}</td><td><a href="${esc(safeUrl(d.source))}" target="_blank" rel="noopener">Bambu Lab ↗</a></td></tr>`).join('')}</tbody></table>`:shown.map(d=>`<article class="card"><div class="picture">${art(d)}<span class="tag">${esc(d.type)}</span></div><div class="body"><small>${esc(d.family)} / ${esc(d.type)}</small><h2>${esc(d.name)}</h2><div class="codes"><div><label>Färgnummer</label><b>${esc(d.code||'Ej angivet')}</b></div><div><label>HEX</label><b>${d.hex.filter(hexOk).map(h=>`<span class="color-dot" style="background:${h}"></span>${esc(h)}`).join('<br>')||'Ej angiven'}</b></div></div>${window.priceLine?window.priceLine(d):''}${ownership(d)}<button class="price-button" data-prices="${esc(ownedKey(d))}">Jämför priser & Tradera ↗</button><a class="source" href="${esc(safeUrl(d.source))}" target="_blank" rel="noopener">Produktsida · ${esc(d.region)} ↗</a></div></article>`).join('')}
$('#search').addEventListener('input',render);$('#type').addEventListener('change',render);$('#sort').addEventListener('change',render);$('#families').addEventListener('click',e=>{if(e.target.dataset.family!==undefined){family=e.target.dataset.family;$('#type').value='';render()}});$('#reset').onclick=()=>{family='';onlyOwned=false;window.onlyPriced=false;window.onlyDeals=false;for(const id of ['#priceFilter','#dealFilter']){const b=document.querySelector(id);b?.setAttribute('aria-pressed',false);b?.classList.remove('active')}if(['price','discount'].includes($('#sort').value))$('#sort').value='type';$('#search').value='';$('#type').value='';render()};$('#view').onclick=()=>{table=!table;$('#view').textContent=table?'Kortvy':'Tabellvy';$('#view').setAttribute('aria-pressed',table);render()};$('#imageMode').onclick=()=>{swatches=!swatches;$('#imageMode').textContent=swatches?'Visa produktbilder':'Visa färgprov';$('#imageMode').setAttribute('aria-pressed',swatches);render()};$('#csv').onclick=()=>{let rows=[['Filamenttyp','Färg','Färgnummer','HEX','Bild','Källa','Jag har','Kvar enligt Bambu (g)'],...shown.map(d=>[d.type,d.name,d.code,d.hex.join(' / '),d.image,d.source,has(d)?'Ja':'Nej',inBambu(d)?inBambu(d).grams:''])];let blob=new Blob(['\uFEFF'+rows.map(r=>r.map(s=>'"'+String(s).replace(/"/g,'""')+'"').join(';')).join('\r\n')],{type:'text/csv;charset=utf-8'});let u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download='bambu-filament.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
fetch('data.json').then(r=>{if(!r.ok)throw Error();return r.json()}).then(d=>{data=d;$('#total').textContent=d.length;$('#types').textContent=new Set(d.map(x=>x.type)).size;$('#type').innerHTML+=[...new Set(d.map(x=>x.type))].sort().map(t=>`<option>${esc(t)}</option>`).join('');render()}).catch(()=>{$('#count').textContent='Katalogen kunde inte läsas. Ladda om sidan för att försöka igen.'});

$('#ownedFilter').onclick=()=>{onlyOwned=!onlyOwned;render()};
$('#results').addEventListener('change',e=>{
 const input=e.target,key=input.dataset.owned;if(key===undefined)return;
 if(input.checked)owned.add(key);else owned.delete(key);
 saveOwned();
 render();const next=Array.from(document.querySelectorAll('[data-owned]')).find(el=>el.dataset.owned===key);if(next)next.focus({preventScroll:true});
});
window.addEventListener('storage',e=>{if(e.key!==storageKey&&e.key!==null)return;try{const saved=JSON.parse(e.newValue||'[]');if(Array.isArray(saved)){owned=new Set(saved.filter(x=>typeof x==='string'));render()}}catch{}});

// Bildfallback utan inline-händelsehanterare (tillåter strikt Content-Security-Policy).
document.addEventListener('error',e=>{const i=e.target;if(i instanceof HTMLImageElement&&i.hasAttribute('data-fallback')){i.style.display='none';if(i.nextElementSibling)i.nextElementSibling.hidden=false}},true);

// --- Inloggad användare: meny och markeringar sparade på kontot ---
let session=null,saveTimer=null,saving=Promise.resolve();
function localSave(){try{localStorage.setItem(storageKey,JSON.stringify([...owned]))}catch{}}
function saveOwned(){
 localSave();
 if(!session){$('#saveStatus').textContent='Sparat i den här webbläsaren.';return}
 $('#saveStatus').textContent='Sparar…';clearTimeout(saveTimer);
 saveTimer=setTimeout(()=>{saving=saving.then(()=>fetch('api/owned',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify({items:[...owned]})}).then(r=>{if(r.status===401){location.href='login?next='+encodeURIComponent(location.pathname);return}if(!r.ok)throw Error();$('#saveStatus').textContent='Sparat på ditt konto ('+session.username+'). Syns på alla dina enheter.'}).catch(()=>{$('#saveStatus').textContent='Kunde inte spara på kontot just nu. Markeringen finns kvar i den här webbläsaren; försök igen senare.'}))},400);
}
function userMenu(){
 const nav=document.createElement('nav');nav.className='usermenu';nav.setAttribute('aria-label','Konto');
 const who=document.createElement('span');who.className='who';who.append('Inloggad som ');const b=document.createElement('b');b.textContent=session.username;who.append(b);nav.append(who);
 const link=(href,text)=>{const a=document.createElement('a');a.href=href;a.textContent=text;nav.append(a)};
 link('konto','Mitt konto');if(session.isAdmin)link('admin','Administration');
 const f=document.createElement('form');f.method='post';f.action='logout';const c=document.createElement('input');c.type='hidden';c.name='csrf';c.value=session.csrf;const btn=document.createElement('button');btn.type='submit';btn.textContent='Logga ut';f.append(c,btn);nav.append(f);
 document.querySelector('header').append(nav);
}
fetch('api/me',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(me=>{
 if(!me)return;session=me;userMenu();
 return fetch('api/owned',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(d=>{
  if(!d)return;
  const server=new Set(d.items.filter(x=>typeof x==='string'));
  // Första gången: flytta markeringar från webbläsaren till kontot.
  const merged=d.saved?server:new Set([...server,...owned]);
  owned=merged;$('#saveStatus').textContent='Dina markeringar sparas på ditt konto och syns på alla dina enheter.';
  if(d.bambu){bambuInfo=d.bambu;bambuOwned=d.bambu.items||{};const n=Object.keys(bambuOwned).length;const when=d.bambu.lastSync?new Date(d.bambu.lastSync*1000).toLocaleString('sv-SE',{dateStyle:'short',timeStyle:'short'}):'aldrig';const warn=d.bambu.status==='expired'?' Inloggningen hos Bambu har gått ut – koppla igen under Mitt konto.':d.bambu.status==='error'?' Senaste synken misslyckades.':'';$('#saveStatus').textContent=`${n} färger kommer från ditt Bambu Lab-bibliotek (synkat ${when}).${warn} Övriga markeringar sparas på ditt konto.`}
  if(!d.saved&&merged.size)saveOwned();else localSave();
  if(data.length)render();
 });
}).catch(()=>{});
