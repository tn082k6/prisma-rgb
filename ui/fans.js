'use strict';
/* =====================================================================
   PAGE: FANS – live temps, profiles, channels, draggable curve editor
   ===================================================================== */
let FAN = null;        // last fan_status result
let FCFG = null;       // editable copy of the service config
let fanTimer = null;
const FAN_PROFILES = [['silent', 'Leise'], ['normal', 'Normal'], ['gaming', 'Gaming'], ['max', 'Volle Leistung']];
const TMIN = 20, TMAX = 100;

async function fanPoll(full) {
  try {
    FAN = await api('fan_status');
    if (FAN.status && (!FCFG || full)) FCFG = JSON.parse(JSON.stringify(FAN.status.config));
  } catch (e) { FAN = null; }
}
function fanStartTimer() {
  clearInterval(fanTimer);
  fanTimer = setInterval(async () => {
    if (UI.page !== 'fans') { clearInterval(fanTimer); fanTimer = null; return; }
    const wasRunning = FAN && FAN.running;
    await fanPoll(false);
    if (!!(FAN && FAN.running) !== !!wasRunning) { await fanPoll(true); renderPage(); return; }
    fanLive();
  }, 1500);
}
function tempVal(id) { const t = FAN && FAN.status && FAN.status.temps.find(x => x.id === id); return t ? t.value : null; }
function fanLive() {
  if (!FAN || !FAN.status) return;
  const st = FAN.status;
  for (const el of $$('[data-ftemp]')) { const v = tempVal(el.dataset.ftemp); el.textContent = v == null ? '–' : v.toFixed(0) + ' °C'; }
  for (const el of $$('[data-frpm]')) { const v = st.fans[el.dataset.frpm]; el.textContent = v == null ? '–' : Math.round(v) + ' U/min'; }
  for (const el of $$('[data-fpct]')) {
    const id = el.dataset.fpct; const v = st.out[id] ?? (st.controls.find(c => c.id === id) || {}).value;
    el.textContent = v == null ? 'BIOS' : Math.round(v) + ' %';
    const bar = el.parentElement.querySelector('.fbar i'); if (bar) bar.style.width = (v || 0) + '%';
  }
  for (const c of (FCFG ? FCFG.curves : [])) {
    const dot = $(`#live-${c.id}`); if (!dot || !c.points) continue;
    const t = tempVal(c.sensor); if (t == null) { dot.style.display = 'none'; continue; }
    dot.style.display = ''; const p = curveAt(c.points, t);
    dot.setAttribute('cx', cx(t)); dot.setAttribute('cy', cy(p));
  }
  const em = $('#fanEmergency'); if (em) em.style.display = st.emergency ? '' : 'none';
}
const cx = t => 30 + (clamp(t, TMIN, TMAX) - TMIN) / (TMAX - TMIN) * 280;
const cy = p => 165 - clamp(p, 0, 100) / 100 * 150;
function curveAt(pts, t) {
  const p = [...pts].sort((a, b) => a[0] - b[0]);
  if (t <= p[0][0]) return p[0][1];
  for (let i = 1; i < p.length; i++) if (t <= p[i][0]) { const [t0, v0] = p[i - 1], [t1, v1] = p[i]; return v0 + (v1 - v0) * (t - t0) / Math.max(1e-6, t1 - t0); }
  return p[p.length - 1][1];
}
let fanSaveT = null;
function fanSave(rerender) {
  clearTimeout(fanSaveT);
  fanSaveT = setTimeout(() => api('fan_config', FCFG).catch(e => toast(e.message, true)), 250);
  if (rerender) renderPage();
}

function pageFans() {
  if (!FAN) { fanPoll(true).then(() => { if (UI.page === 'fans') renderPage(); }); return `<div class="page-head"><div><h1>Lüfter</h1><p>Lade …</p></div></div>`; }
  afterHooks.push(fanStartTimer);
  const head = `<div class="page-head"><div><h1>Lüfter</h1><p>Temperaturgesteuerte Lüfterkurven – direkt in Prisma.</p></div><div class="grow"></div>`;
  if (!FAN.running) {
    return head + `</div><div class="card panel" style="max-width:720px;gap:14px;display:flex;flex-direction:column">
      <h4>Lüftersteuerung einrichten</h4>
      <p class="muted" style="margin:0;line-height:1.6">Prisma regelt die Lüfter über einen kleinen Dienst mit Adminrechten. Beim Einrichten
      <b>übernimmt Prisma deine FanControl-Kurven</b>, beendet FanControl und deaktiviert dessen Autostart (nichts wird gelöscht).
      Windows fragt dafür einmal nach Adminrechten.</p>
      <div class="note"><b>Sicherungen:</b> Fällt ein Temperatursensor aus → dieser Lüfter 100 %. Ab der Notfall-Temperatur (90 °C) → alle Lüfter 100 %.
      Bei einem Fehler im Dienst → 100 %. Beim Beenden übernimmt wieder das BIOS. Kein Lüfter fällt unter seine Mindestdrehzahl.</div>
      ${FAN.service_wanted ? '<div class="note warn">Der Dienst wird gestartet … falls das länger dauert, auf „Einrichten“ klicken.</div>' : ''}
      <div class="row"><button class="btn primary" data-act="fan-setup">${icon('fan')} Lüftersteuerung einrichten</button></div></div>`;
  }
  const st = FAN.status, cfg = FCFG;
  const pick = re => st.temps.find(t => re.test(t.name));
  const tiles = [pick(/CPU Package|Tctl|Tdie/), pick(/Core Max/), pick(/^GPU Core|GPU Temperature/), pick(/Hot Spot/)].filter(Boolean);
  const tempOpts = sel => st.temps.map(t => `<option value="${esc(t.id)}" ${t.id === sel ? 'selected' : ''}>${esc(t.hw)} · ${esc(t.name)}</option>`).join('');
  const curveOpts = sel => cfg.curves.map(c => `<option value="${esc(c.id)}" ${c.id === sel ? 'selected' : ''}>${esc(c.name)}</option>`).join('');
  return head + `
    <div class="row"><span class="dim" style="font-size:12px">Prisma regelt</span><label class="switch"><input type="checkbox" data-fanen ${cfg.enabled ? 'checked' : ''}><span></span></label></div></div>
    <div class="banner" id="fanEmergency" style="${st.emergency ? '' : 'display:none'}">${icon('warn')}<div class="grow"><b>Notfall: Temperatur zu hoch – alle Lüfter auf 100 %</b><span>Läuft automatisch zurück, sobald es 5 °C kühler ist.</span></div></div>
    ${!cfg.enabled ? `<div class="banner info">${icon('info')}<div class="grow"><b>Prisma regelt die Lüfter noch nicht</b><span>Gerade steuert das BIOS. Prüfe die übernommenen Kurven und schalte dann oben rechts „Prisma regelt“ ein.</span></div></div>` : ''}
    <div class="fan-top">
      ${tiles.map(t => `<div class="card ftile"><span class="dim">${esc(t.name)}</span><b data-ftemp="${esc(t.id)}">${t.value.toFixed(0)} °C</b><small>${esc(t.hw)}</small></div>`).join('')}
      <div class="card ftile" style="flex:2"><span class="dim">Profil</span><div class="seg">${FAN_PROFILES.map(([k, l]) => `<button data-fprof="${k}" class="${cfg.profile === k ? 'on' : ''}">${l}</button>`).join('')}</div>
        <small>Leise −12 % · Gaming +15 % · Volle Leistung 100 % (Pumpe ausgenommen)</small></div>
    </div>
    <div class="fan-grid">
      <div class="card panel"><h4>Lüfter <button class="btn sm" data-act="fan-import">${icon('download')} Aus FanControl übernehmen</button></h4>
        ${cfg.channels.map((ch, i) => `<div class="fch" data-ch="${i}">
          <div class="row between"><input type="text" class="fname" data-chname value="${esc(ch.name)}">
            <div class="row" style="gap:14px"><span class="dim" data-frpm="${esc(ch.fan || '')}">–</span><b data-fpct="${esc(ch.id)}" style="min-width:52px;text-align:right">–</b></div></div>
          <div class="fbar"><i></i></div>
          <div class="row wrap" style="gap:12px">
            <div class="seg">${[['curve', 'Kurve'], ['fixed', 'Fest'], ['auto', 'BIOS']].map(([m, l]) => `<button data-chmode="${m}" class="${ch.mode === m ? 'on' : ''}">${l}</button>`).join('')}</div>
            ${ch.mode === 'curve' ? `<select data-chcurve>${curveOpts(ch.curve)}</select>` : ''}
            ${ch.mode === 'fixed' ? `<div class="range" style="min-width:180px"><input type="range" min="0" max="100" value="${ch.fixed}" data-chfixed style="--p:${ch.fixed}%"><output>${Math.round(ch.fixed)} %</output></div>` : ''}
          </div>
          ${ch.mode !== 'auto' ? `<div class="row wrap" style="gap:16px"><div class="range" style="min-width:200px"><span class="dim" style="font-size:11.5px;width:70px">Minimum</span><input type="range" min="0" max="80" value="${ch.min}" data-chmin style="--p:${ch.min / 0.8}%"><output>${Math.round(ch.min)} %</output></div>
            <label class="row" style="gap:8px;font-size:12px" class="dim"><span class="dim">Profil gilt</span><span class="switch"><input type="checkbox" data-chprof ${ch.profile !== false ? 'checked' : ''}><span></span></span></label></div>` : ''}
        </div>`).join('') || '<div class="dim">Keine Lüfter gefunden.</div>'}
      </div>
      <div class="col" style="display:flex;flex-direction:column;gap:14px">
        ${cfg.curves.map((c, i) => curveCard(c, i, tempOpts)).join('')}
        <div class="row"><button class="btn primary sm" data-act="fan-addcurve">${icon('plus')} Neue Kurve</button><button class="btn sm" data-act="fan-addfixed">${icon('plus')} Fester Wert</button></div>
        <div class="card panel"><h4>Sicherheit</h4><div class="range"><span class="dim" style="font-size:12px;width:120px">Notfall ab</span><input type="range" min="75" max="100" value="${cfg.safety.emergency_temp}" data-femer style="--p:${(cfg.safety.emergency_temp - 75) / 25 * 100}%"><output>${cfg.safety.emergency_temp} °C</output></div></div>
      </div>
    </div>`;
}
const CURVE_PRESETS = {
  leise: ['Leise', [[30, 25], [50, 30], [65, 45], [75, 70], [85, 100]]],
  ausgewogen: ['Ausgewogen', [[30, 30], [50, 40], [65, 60], [75, 85], [85, 100]]],
  leistung: ['Leistung', [[30, 40], [50, 55], [60, 75], [70, 100]]],
};
function curveCard(c, i, tempOpts) {
  const users = FCFG.channels.filter(ch => ch.mode === 'curve' && ch.curve === c.id).map(ch => ch.name);
  const head = `<h4><input type="text" class="fname" data-cvname value="${esc(c.name)}"><button class="btn sm ghost" data-cvdel title="Kurve löschen">${icon('trash')}</button></h4>
    <div class="dim" style="font-size:12px;margin:-6px 0 12px">${users.length ? 'Genutzt von: ' + users.map(esc).join(', ') : 'Wird noch von keinem Lüfter genutzt'}</div>`;
  if (!c.points) {
    return `<div class="card panel" data-cv="${i}">${head}<div class="field"><span class="lbl">Fester Wert</span><div class="range"><input type="range" min="0" max="100" value="${c.fixed}" data-cvfixed style="--p:${c.fixed}%"><output>${Math.round(c.fixed)} %</output></div></div></div>`;
  }
  const pts = [...c.points].sort((a, b) => a[0] - b[0]);
  return `<div class="card panel" data-cv="${i}">${head}
    <div class="field" style="margin-bottom:12px"><span class="lbl">Temperatur von</span><select data-cvsensor>${tempOpts(c.sensor)}</select></div>
    <div class="row wrap" style="gap:6px;margin-bottom:10px"><span class="dim" style="font-size:12px">Vorlage:</span>${Object.entries(CURVE_PRESETS).map(([k, [l]]) => `<button class="chip" data-preset="${k}">${l}</button>`).join('')}</div>
    ${curveSvg(c)}
    <div class="ptable">
      <div class="ph"><span>Temperatur</span><span>Lüfter</span><span></span></div>
      ${pts.map((p, k) => `<div class="pr" data-pi="${k}"><label><input type="number" min="${TMIN}" max="${TMAX}" value="${Math.round(p[0])}" data-ptt> °C</label>
        <label><input type="number" min="0" max="100" value="${Math.round(p[1])}" data-ptp> %</label>
        <button class="btn sm ghost icon" data-ptdel ${pts.length <= 2 ? 'disabled' : ''} title="Punkt entfernen">${icon('close')}</button></div>`).join('')}
    </div>
    <div class="row between" style="margin-top:10px"><button class="btn sm" data-ptadd>${icon('plus')} Punkt</button>
      <label class="row" style="gap:8px"><span class="dim" style="font-size:12px">Hysterese</span><input type="number" min="0" max="10" step="1" value="${c.hyst ?? 2}" data-cvhyst style="width:58px"><span class="dim" style="font-size:12px">°C</span></label></div>
    <div class="hint" style="margin-top:8px">Punkte in der Grafik ziehen oder unten genau eintippen. Hysterese = um so viele Grad muss es abkühlen, bevor die Lüfter leiser werden (verhindert Pumpen).</div>
  </div>`;
}
function curveSvg(c, active = -1) {
  const pts = [...c.points].sort((a, b) => a[0] - b[0]);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${cx(p[0]).toFixed(1)},${cy(p[1]).toFixed(1)}`).join(' ');
  const area = path + ` L${cx(pts[pts.length - 1][0])},165 L${cx(pts[0][0])},165 Z`;
  let grid = '';
  for (let t = 20; t <= 100; t += 20) grid += `<line x1="${cx(t)}" y1="15" x2="${cx(t)}" y2="165" class="g"/><text x="${cx(t)}" y="178" class="l">${t}°</text>`;
  for (let p = 0; p <= 100; p += 25) grid += `<line x1="30" y1="${cy(p)}" x2="310" y2="${cy(p)}" class="g"/><text x="24" y="${cy(p) + 3}" class="l" text-anchor="end">${p}%</text>`;
  return `<svg class="fcurve" viewBox="0 0 320 184" data-svg>${grid}<path d="${area}" class="a"/><path d="${path}" class="p"/>
    ${pts.map((p, i) => `<circle cx="${cx(p[0])}" cy="${cy(p[1])}" r="${i === active ? 8 : 6}" class="pt${i === active ? ' on' : ''}" data-pi="${i}"><title>${p[0].toFixed(0)} °C → ${p[1].toFixed(0)} %</title></circle>`).join('')}
    ${active >= 0 && pts[active] ? (() => { const p = pts[active], x = clamp(cx(p[0]), 50, 290), y = Math.max(28, cy(p[1]) - 14);
      return `<rect x="${x - 34}" y="${y - 12}" width="68" height="17" rx="4" class="tip"/><text x="${x}" y="${y}" class="tipt">${p[0].toFixed(0)} °C · ${p[1].toFixed(0)} %</text>`; })() : ''}
    <circle id="live-${c.id}" r="4.5" class="live" cx="-10" cy="-10"/></svg>`;
}

/* ---------- events ---------- */
document.addEventListener('click', async e => {
  if (UI.page !== 'fans') return;
  const t = e.target.closest('[data-fprof],[data-chmode],[data-cvdel],[data-preset],[data-ptdel],[data-ptadd]'); if (!t) return;
  const card = t.closest('[data-cv]'), cv = card && FCFG.curves[+card.dataset.cv];
  if (t.dataset.preset && cv) { cv.points = CURVE_PRESETS[t.dataset.preset][1].map(p => [...p]); toast(`Vorlage „${CURVE_PRESETS[t.dataset.preset][0]}“ übernommen`); return fanSave(true); }
  if (t.dataset.ptdel !== undefined && cv) { cv.points.sort((a, b) => a[0] - b[0]); cv.points.splice(+t.closest('[data-pi]').dataset.pi, 1); return fanSave(true); }
  if (t.dataset.ptadd !== undefined && cv) {
    cv.points.sort((a, b) => a[0] - b[0]);
    // insert in the widest temperature gap, or extend to the right
    let best = -1, gap = 0;
    for (let k = 1; k < cv.points.length; k++) { const g = cv.points[k][0] - cv.points[k - 1][0]; if (g > gap) { gap = g; best = k; } }
    if (gap >= 6) { const [a, b] = [cv.points[best - 1], cv.points[best]]; cv.points.splice(best, 0, [Math.round((a[0] + b[0]) / 2), Math.round((a[1] + b[1]) / 2)]); }
    else { const l = cv.points[cv.points.length - 1]; cv.points.push([Math.min(TMAX, l[0] + 10), Math.min(100, l[1] + 10)]); }
    return fanSave(true);
  }
  if (t.dataset.fprof) { FCFG.profile = t.dataset.fprof; await api('fan_profile', t.dataset.fprof); return renderPage(); }
  const chEl = t.closest('[data-ch]'), cvEl = t.closest('[data-cv]');
  if (t.dataset.chmode && chEl) { FCFG.channels[+chEl.dataset.ch].mode = t.dataset.chmode; if (t.dataset.chmode === 'curve' && !FCFG.channels[+chEl.dataset.ch].curve) FCFG.channels[+chEl.dataset.ch].curve = FCFG.curves[0]?.id; return fanSave(true); }
  if (t.dataset.cvdel !== undefined && cvEl) {
    const c = FCFG.curves[+cvEl.dataset.cv];
    if (FCFG.channels.some(ch => ch.mode === 'curve' && ch.curve === c.id)) return toast('Kurve wird noch von einem Lüfter benutzt', true);
    FCFG.curves.splice(+cvEl.dataset.cv, 1); return fanSave(true);
  }
});
document.addEventListener('change', async e => {
  if (UI.page !== 'fans') return;
  const t = e.target, d = t.dataset, chEl = t.closest('[data-ch]'), cvEl = t.closest('[data-cv]');
  if (d.fanen !== undefined) { FCFG.enabled = t.checked; await api('fan_enable', t.checked); toast(t.checked ? 'Prisma regelt jetzt die Lüfter' : 'Lüfter wieder beim BIOS'); return renderPage(); }
  if (d.chname !== undefined) { FCFG.channels[+chEl.dataset.ch].name = t.value; return fanSave(); }
  if (d.chcurve !== undefined) { FCFG.channels[+chEl.dataset.ch].curve = t.value; return fanSave(); }
  if (d.chprof !== undefined) { FCFG.channels[+chEl.dataset.ch].profile = t.checked; return fanSave(); }
  if (d.cvname !== undefined) { FCFG.curves[+cvEl.dataset.cv].name = t.value; return fanSave(); }
  if (d.cvsensor !== undefined) { FCFG.curves[+cvEl.dataset.cv].sensor = t.value; return fanSave(); }
  if (d.cvhyst !== undefined) { FCFG.curves[+cvEl.dataset.cv].hyst = clamp(+t.value || 0, 0, 10); return fanSave(); }
  if ((d.ptt !== undefined || d.ptp !== undefined) && cvEl) {
    const c = FCFG.curves[+cvEl.dataset.cv]; c.points.sort((a, b) => a[0] - b[0]);
    const k = +t.closest('[data-pi]').dataset.pi;
    if (d.ptt !== undefined) c.points[k][0] = clamp(Math.round(+t.value), TMIN, TMAX);
    else c.points[k][1] = clamp(Math.round(+t.value), 0, 100);
    c.points.sort((a, b) => a[0] - b[0]);
    return fanSave(true);
  }
});
document.addEventListener('input', e => {
  if (UI.page !== 'fans') return;
  const t = e.target, d = t.dataset, chEl = t.closest('[data-ch]'), cvEl = t.closest('[data-cv]');
  const out = v => { if (t.nextElementSibling) t.nextElementSibling.textContent = v; };
  if (d.chfixed !== undefined) { FCFG.channels[+chEl.dataset.ch].fixed = +t.value; out(t.value + ' %'); return fanSave(); }
  if (d.chmin !== undefined) { FCFG.channels[+chEl.dataset.ch].min = +t.value; t.style.setProperty('--p', (t.value / 0.8) + '%'); out(t.value + ' %'); return fanSave(); }
  if (d.cvfixed !== undefined) { FCFG.curves[+cvEl.dataset.cv].fixed = +t.value; out(t.value + ' %'); return fanSave(); }
  if (d.femer !== undefined) { FCFG.safety.emergency_temp = +t.value; t.style.setProperty('--p', ((t.value - 75) / 25 * 100) + '%'); out(t.value + ' °C'); return fanSave(); }
});
// curve point dragging
function svgPoint(svg, ev) { const r = svg.getBoundingClientRect(); return [(ev.clientX - r.left) / r.width * 320, (ev.clientY - r.top) / r.height * 184]; }
const toT = x => clamp(TMIN + (x - 30) / 280 * (TMAX - TMIN), TMIN, TMAX), toP = y => clamp((165 - y) / 150 * 100, 0, 100);
document.addEventListener('pointerdown', e => {
  const pt = e.target.closest('.fcurve .pt'); if (!pt || e.button !== 0) return;
  e.preventDefault();
  const card = pt.closest('[data-cv]'), c = FCFG.curves[+card.dataset.cv];
  c.points.sort((a, b) => a[0] - b[0]);
  const i = +pt.dataset.pi;
  const move = ev => {
    const svg = card.querySelector('svg');  // the graphic is re-rendered while dragging – always measure the live one
    const [x, y] = svgPoint(svg, ev);
    const lo = i > 0 ? c.points[i - 1][0] + 1 : TMIN, hi = i < c.points.length - 1 ? c.points[i + 1][0] - 1 : TMAX;
    c.points[i] = [Math.round(clamp(toT(x), lo, hi)), Math.round(toP(y))];
    svg.outerHTML = curveSvg(c, i); fanSave();
  };
  const up = () => { document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); renderPage(); };
  document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
});
document.addEventListener('dblclick', e => {
  const svg = e.target.closest('.fcurve'); if (!svg) return;
  const c = FCFG.curves[+svg.closest('[data-cv]').dataset.cv]; const [x, y] = svgPoint(svg, e);
  c.points.push([Math.round(toT(x)), Math.round(toP(y))]); c.points.sort((a, b) => a[0] - b[0]); fanSave(true);
});
document.addEventListener('contextmenu', e => {
  const pt = e.target.closest('.fcurve .pt'); if (!pt) return; e.preventDefault();
  const c = FCFG.curves[+pt.closest('[data-cv]').dataset.cv]; if (c.points.length <= 2) return toast('Mindestens 2 Punkte', true);
  c.points.sort((a, b) => a[0] - b[0]); c.points.splice(+pt.dataset.pi, 1); fanSave(true);
});
async function fanAction(a) {
  if (a === 'fan-setup') { const r = await api('fan_setup'); toast(r.msg, !r.ok); setTimeout(async () => { await fanPoll(true); renderPage(); }, 6000); return; }
  if (a === 'fan-import') { if (!confirm('Kurven und Lüfter aus FanControl neu übernehmen? Eigene Änderungen gehen verloren.')) return; await api('fan_import'); await fanPoll(true); toast('Aus FanControl übernommen'); return renderPage(); }
  if (a === 'fan-addcurve') {
    const cpu = FAN.status.temps.find(t => /CPU Package|Tctl/.test(t.name)) || FAN.status.temps[0] || {};
    FCFG.curves.push({ id: 'k' + Date.now().toString(36), name: 'Kurve ' + (FCFG.curves.length + 1), sensor: cpu.id,
      points: CURVE_PRESETS.ausgewogen[1].map(p => [...p]), hyst: 2 });
    fanSave(true); toast('Neue Kurve angelegt – unten bei „Lüfter“ einem Lüfter zuweisen');
    setTimeout(() => { const cards = $$('[data-cv]'); cards[cards.length - 1]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 60);
    return;
  }
  if (a === 'fan-addfixed') { FCFG.curves.push({ id: 'f' + Date.now().toString(36), name: 'Fester Wert', fixed: 60 }); return fanSave(true); }
}
