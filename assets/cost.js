// Kostnadsräknaren: fyll i effekt från vald skrivare och läs in filamentåtgång och tid från en slicer-fil.
// Filen läses bara i webbläsaren (inget laddas upp). Inga inline-skript: strikt Content-Security-Policy.
(() => {
  const $ = s => document.querySelector(s);
  const printer = $('.cost-printer');
  if (printer) printer.addEventListener('change', () => { if (printer.value) $('#cw').value = printer.value; });
  const file = $('#costFile'), status = $('#costFileStatus');
  if (!file) return;
  const say = t => { status.textContent = t; };

  // --- zip (3MF) utan bibliotek: central katalog + DecompressionStream('deflate-raw') ---
  async function zipEntries(f) {
    const tail = new DataView(await f.slice(Math.max(0, f.size - 66000)).arrayBuffer());
    let eocd = -1;
    for (let i = tail.byteLength - 22; i >= 0; i--) if (tail.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw Error('ingen zip');
    const count = tail.getUint16(eocd + 10, true), size = tail.getUint32(eocd + 12, true), offset = tail.getUint32(eocd + 16, true);
    const cd = new DataView(await f.slice(offset, offset + size).arrayBuffer());
    const out = {};
    for (let p = 0, n = 0; n < count && p + 46 <= cd.byteLength; n++) {
      const method = cd.getUint16(p + 10, true), csize = cd.getUint32(p + 20, true), nlen = cd.getUint16(p + 28, true),
        xlen = cd.getUint16(p + 30, true), clen = cd.getUint16(p + 32, true), local = cd.getUint32(p + 42, true);
      const name = new TextDecoder().decode(new Uint8Array(cd.buffer, cd.byteOffset + p + 46, nlen));
      out[name] = { method, csize, local };
      p += 46 + nlen + xlen + clen;
    }
    return out;
  }
  async function zipText(f, entry) {
    const head = new DataView(await f.slice(entry.local, entry.local + 30).arrayBuffer());
    const start = entry.local + 30 + head.getUint16(26, true) + head.getUint16(28, true);
    const blob = f.slice(start, start + entry.csize);
    if (entry.method === 0) return blob.text();
    if (entry.method !== 8) throw Error('okänd komprimering');
    return new Response(blob.stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
  }
  const profileType = s => String(s || '').replace(/^Bambu\s+/i, '').replace(/\s*@.*$/, '').trim();

  async function read3mf(f) {
    const files = await zipEntries(f);
    const info = files['Metadata/slice_info.config'];
    if (!info) throw Error('Filen är inte slicad. Exportera med "Export plate sliced file" (.gcode.3mf) i Bambu Studio.');
    const xml = new DOMParser().parseFromString(await zipText(f, info), 'application/xml');
    let settings = {};
    if (files['Metadata/project_settings.config']) {
      try { settings = JSON.parse(await zipText(f, files['Metadata/project_settings.config'])); } catch {}
    }
    const used = new Map(); let seconds = 0, plates = 0;
    for (const plate of xml.querySelectorAll('plate')) {
      const pred = plate.querySelector('metadata[key="prediction"]');
      if (!pred) continue;
      plates++; seconds += parseFloat(pred.getAttribute('value')) || 0;
      for (const fl of plate.querySelectorAll('filament')) {
        const id = fl.getAttribute('id'), g = parseFloat(fl.getAttribute('used_g')) || 0;
        const prev = used.get(id) || { g: 0, type: fl.getAttribute('type'), color: fl.getAttribute('color'), idx: fl.getAttribute('tray_info_idx') };
        prev.g += g; used.set(id, prev);
      }
    }
    if (!plates) throw Error('Ingen slicad platta hittades i filen.');
    const rows = [...used.entries()].filter(([, v]) => v.g > 0).map(([id, v]) => {
      const i = parseInt(id, 10) - 1;
      return { grams: v.g, color: v.color, id: v.idx || (settings.filament_ids || [])[i],
        type: profileType((settings.filament_settings_id || [])[i]) || v.type };
    });
    return { rows, seconds, note: plates > 1 ? `${plates} plattor` : '' };
  }

  async function readGcode(f) {
    const text = (await f.slice(0, 300000).text()) + '\n' + (await f.slice(Math.max(0, f.size - 400000)).text());
    const val = name => { const m = text.match(new RegExp('^;\\s*' + name + '\\s*[=:]\\s*(.+)$', 'mi')); return m ? m[1].trim() : ''; };
    const list = s => s.split(/[;,]/).map(x => x.trim().replace(/^"|"$/g, ''));
    const grams = list(val('total filament weight \\[g\\]') || val('filament used \\[g\\]')).map(parseFloat);
    if (!grams.some(g => g > 0)) throw Error('Hittade ingen filamentåtgång i G-koden.');
    const colors = list(val('filament_colour')), ids = list(val('filament_ids')), types = list(val('filament_settings_id')),
      plain = list(val('filament_type'));
    const t = (val('model printing time') || val('total estimated time') || val('estimated printing time \\(normal mode\\)')).split(';')[0];
    let seconds = 0;
    for (const [, n, u] of (t.match(/\d+\s*[dhms]/g) || []).map(x => x.match(/(\d+)\s*([dhms])/))) seconds += n * { d: 86400, h: 3600, m: 60, s: 1 }[u];
    const rows = grams.map((g, i) => ({ grams: g, color: colors[i], id: ids[i], type: profileType(types[i]) || plain[i] })).filter(r => r.grams > 0);
    return { rows, seconds, note: '' };
  }

  file.addEventListener('change', async () => {
    const f = file.files[0];
    if (!f) return;
    say('Läser filen…');
    try {
      const isZip = new Uint8Array(await f.slice(0, 2).arrayBuffer()).join() === '80,75';
      const r = isZip ? await read3mf(f) : await readGcode(f);
      // Katalogfärg via samma matchning som Bambu-synken (filament-id + färg).
      let keys = [];
      try {
        const csrf = document.querySelector('input[name=csrf]').value;
        const res = await fetch('/api/match', { method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ items: r.rows.slice(0, 8).map(x => ({ id: x.id, type: x.type, color: x.color })) }) });
        if (res.ok) keys = (await res.json()).keys;
      } catch {}
      for (let i = 1; i <= 8; i++) {
        const row = r.rows[i - 1], sel = document.querySelector(`[name=k${i}]`), g = document.querySelector(`[name=g${i}]`);
        if (!sel || !g) continue;
        sel.value = row && keys[i - 1] ? keys[i - 1] : '';
        g.value = row ? (Math.round(row.grams * 10) / 10).toString().replace('.', ',') : '';
      }
      if (r.rows.length > 4) document.querySelectorAll('#costForm details').forEach(d => { d.open = true; });
      if (r.seconds) { $('#ch').value = Math.floor(r.seconds / 3600); $('#cm').value = Math.round((r.seconds % 3600) / 60); }
      const unknown = r.rows.slice(0, 8).filter((_, i) => !keys[i]).length;
      const total = r.rows.reduce((s, x) => s + x.grams, 0);
      say(`Inläst: ${r.rows.length} ${r.rows.length === 1 ? 'filament' : 'filament'}, ${(Math.round(total * 10) / 10).toString().replace('.', ',')} g` +
        (r.seconds ? `, ${Math.floor(r.seconds / 3600)} h ${Math.round((r.seconds % 3600) / 60)} min` : '') + (r.note ? ` (${r.note})` : '') + '.' +
        (unknown ? ` ${unknown} ${unknown === 1 ? 'färg' : 'färger'} kunde inte kopplas till katalogen – välj själv.` : '') +
        (r.rows.length > 8 ? ' Bara de 8 första filamenten fick plats.' : '') + ' Tryck Räkna ut.');
    } catch (err) {
      say('Filen kunde inte läsas: ' + (err.message || err));
    }
  });
})();
