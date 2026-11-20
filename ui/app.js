'use strict';
/* =====================================================================
   Prisma UI
   ===================================================================== */
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

async function api(name, ...args) {
  const r = await fetch('/api/' + name, { method: 'POST', headers: { 'X-Prisma-Token': TOKEN, 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || 'Fehler');
  return j.result;
}

let S = null;          // full state from backend
let FRAME = null;      // last frame
const BYTES = {};      // devKey -> Uint8Array
const UI = { page: 'scenes', cat: 'Alle', q: '', editId: null, layerId: null, tab: 'effect', fxCat: null };

const TYPE_LABEL = { motherboard: 'Mainboard', dram: 'Arbeitsspeicher', gpu: 'Grafikkarte', cooler: 'Kühler', ledstrip: 'LED-Streifen', keyboard: 'Tastatur', mouse: 'Maus', mousemat: 'Mauspad', headset: 'Headset', headset_stand: 'Headset-Ständer', gamepad: 'Controller', light: 'Licht', speaker: 'Lautsprecher', virtual: 'Virtuell', storage: 'Speicher', case: 'Gehäuse', microphone: 'Mikrofon', accessory: 'Zubehör', keypad: 'Keypad' };
const TYPE_ICON = t => ICONS[t] ? t : (t === 'mousemat' ? 'accessory' : t === 'headset_stand' ? 'headset' : t === 'case' ? 'cooler' : 'devices');
const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

/* ------------------------------------------------------------ toasts */
function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.innerHTML = `<i></i><span>${esc(msg)}</span>`;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.style.transition = '.3s'; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, 2600);
}
async function safe(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ helpers */
const effect = id => S.effects.find(e => e.id === id);
const scene = id => S.scenes.find(s => s.id === id);
const device = key => S.devices.find(d => d.key === key);
const devName = d => (d.cfg && d.cfg.alias) || d.name;
function palColors(v) {
  if (Array.isArray(v)) return v;
  return (S.palettes[v] || S.palettes.rainbow).colors;
}
function hexLum(h) { const n = parseInt(h.slice(1), 16); return ((n >> 16) * .3 + ((n >> 8) & 255) * .59 + (n & 255) * .11) / 255; }
function sceneColors(sc) {
  const out = [];
  for (const ly of sc.layers || []) {
    const ef = effect(ly.effect); if (!ef) continue;
    for (const p of ef.params) {
      const v = ly.params[p.key];
      if (p.type === 'palette') out.push(...palColors(v));
      else if (p.type === 'color' && p.key !== 'bg') out.push(v);
    }
    if (ly.effect === 'rainbow') out.push('#ff0040', '#ffcc00', '#00ff80', '#0080ff', '#a000ff');
    if (ly.effect === 'white' || ly.effect === 'daylight') out.push('#ffd9a8', '#fff3e0');
    if (ly.effect === 'cpu') out.push('#00ff40', '#ffd000', '#ff0000');
  }
  const uniq = [...new Set(out.map(c => c.toLowerCase()))].filter(c => hexLum(c) > .06);
  return uniq.length ? uniq.slice(0, 6) : ['#1a1a24', '#0d0d14'];
}
function gradCss(cols, dir = '120deg') { return cols.length === 1 ? cols[0] : `linear-gradient(${dir}, ${cols.join(', ')})`; }
function targetLabel(t) {
  t = t || { type: 'all' };
  if (t.type === 'all') return ['all', 'Alle Geräte'];
  if (t.type === 'fans') return ['fan', 'Alle Lüfter'];
  if (t.type === 'devtype') return [TYPE_ICON(t.dtype), 'Alle: ' + (TYPE_LABEL[t.dtype] || t.dtype)];
  const d = device(t.key);
  if (t.type === 'device') return [d ? TYPE_ICON(d.type) : 'devices', d ? devName(d) : 'Gerät nicht verbunden'];
  if (t.type === 'zone') { const z = d && d.zones[t.zone]; return ['ledstrip', d ? `${devName(d)} · ${z ? z.name : 'Zone ' + t.zone}` : 'Zone nicht verbunden']; }
  return ['all', '?'];
}

/* ===================================================================
   LIVE RIG RENDERING
   =================================================================== */
let spin = 0;
function zoneOffset(dev, zi) { let o = 0; for (let i = 0; i < zi; i++) o += dev.zones[i].leds; return o; }
function rgbAt(bytes, i) { return bytes ? [bytes[i * 3], bytes[i * 3 + 1], bytes[i * 3 + 2]] : [0, 0, 0]; }
const css = c => `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
const lum = c => (c[0] * .3 + c[1] * .59 + c[2] * .11) / 255;

function visualFor(dev, compact) {
  // returns html snippet with canvases describing how to draw this device
  const size = compact ? .78 : 1;
  if (dev.cfg.control && dev.cfg.control.startsWith('hw:')) return `<div class="hwmode">Hardware-Effekt: ${esc(dev.cfg.control.slice(3))}</div>`;
  if (dev.type === 'mouse') return `<canvas data-viz="mouse" data-dev="${esc(dev.key)}" data-w="${Math.round(74 * size)}" data-h="${Math.round(118 * size)}"></canvas>`;
  if (dev.leds <= 1 || (dev.type === 'accessory' && dev.leds <= 2)) return `<canvas data-viz="puck" data-dev="${esc(dev.key)}" data-w="${Math.round(110 * size)}" data-h="${Math.round(60 * size)}"></canvas>`;
  return dev.zones.map(z => {
    const zc = dev.cfg.zones[z.index] || {};
    if (z.leds <= 0) return '';
    let kind = zc.layout || 'strip', w, h;
    if (dev.type === 'dram') { kind = 'stick'; w = 30 * size; h = 150 * size; }
    else if (kind === 'fans') { const f = Math.max(1, Math.min(zc.fans || 1, z.leds)); const s = 104 * size; w = f * s + (f - 1) * 10; h = s; }
    else if (kind === 'single' || z.leds === 1) { kind = 'single'; w = 46 * size; h = 46 * size; }
    else { w = clamp(z.leds * 13, 80, 460) * size; h = 26 * size; }
    return `<div class="z"><canvas data-viz="${kind}" data-dev="${esc(dev.key)}" data-zone="${z.index}" data-w="${Math.round(w)}" data-h="${Math.round(h)}"></canvas><small>${esc(z.name)}</small></div>`;
  }).join('');
}
function rigHtml(compact = false, filterKey = null) {
  if (!S.devices.length) return `<div class="empty">${icon('devices')}<div>Keine Geräte gefunden</div></div>`;
  const devs = filterKey ? S.devices.filter(d => d.key === filterKey) : S.devices;
  return `<div class="rig ${compact ? 'compact' : ''}">${devs.map(d => `<div class="dv"><div class="zones">${visualFor(d, compact)}</div><div class="name">${esc(devName(d))}</div></div>`).join('')}</div>`;
}
function setupCanvases(root = document) {
  const dpr = window.devicePixelRatio || 1;
  for (const c of $$('canvas[data-viz]', root)) {
    if (c._ready) continue;
    const w = +c.dataset.w, h = +c.dataset.h;
    c.width = w * dpr; c.height = h * dpr; c.style.width = w + 'px'; c.style.height = h + 'px';
    c._ctx = c.getContext('2d'); c._ctx.scale(dpr, dpr); c._w = w; c._h = h; c._ready = true;
  }
}
function drawAll() {
  for (const c of $$('canvas[data-viz]')) {
    if (!c._ready) continue;
    const dev = device(c.dataset.dev); if (!dev) continue;
    const bytes = DISP[dev.key] || BYTES[dev.key];
    const ctx = c._ctx; ctx.clearRect(0, 0, c._w, c._h);
    const kind = c.dataset.viz;
    if (kind === 'mouse') drawMouse(ctx, c._w, c._h, dev, bytes);
    else if (kind === 'puck') drawPuck(ctx, c._w, c._h, dev, bytes);
    else {
      const zi = +c.dataset.zone, z = dev.zones[zi]; if (!z) continue;
      const off = zoneOffset(dev, zi), cols = [];
      for (let i = 0; i < z.leds; i++) cols.push(rgbAt(bytes, off + i));
      const zc = dev.cfg.zones[zi] || {};
      if (kind === 'fans') drawFans(ctx, c._w, c._h, cols, Math.max(1, Math.min(zc.fans || 1, z.leds)));
      else if (kind === 'stick') drawStick(ctx, c._w, c._h, cols);
      else if (kind === 'single') drawSingle(ctx, c._w, c._h, cols[0]);
      else drawStrip(ctx, c._w, c._h, cols);
    }
  }
}
function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }
function avg(cols) { if (!cols.length) return [0, 0, 0]; const a = [0, 0, 0]; for (const c of cols) { a[0] += c[0]; a[1] += c[1]; a[2] += c[2]; } return a.map(v => Math.round(v / cols.length)); }

function drawFans(ctx, W, H, cols, fans) {
  const n = cols.length, per = n / fans, s = H, gap = 10;
  for (let f = 0; f < fans; f++) {
    const x0 = f * (s + gap), cx = x0 + s / 2, cy = s / 2, R = s * .39;
    const seg = cols.slice(Math.round(f * per), Math.round((f + 1) * per));
    const a = avg(seg);
    // housing
    rr(ctx, x0 + 1, 1, s - 2, s - 2, s * .16); ctx.fillStyle = '#0a0a11'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.lineWidth = 1; ctx.stroke();
    for (const [mx, my] of [[.1, .1], [.9, .1], [.1, .9], [.9, .9]]) { ctx.beginPath(); ctx.arc(x0 + s * mx, s * my, s * .028, 0, 7); ctx.fillStyle = '#1b1b26'; ctx.fill(); }
    // inner glow
    const g = ctx.createRadialGradient(cx, cy, R * .2, cx, cy, R * 1.25);
    g.addColorStop(0, `rgba(${a[0]},${a[1]},${a[2]},.30)`); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, 7); ctx.fill();
    // blades
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * (f % 2 ? 1 : 1));
    for (let b = 0; b < 7; b++) {
      ctx.rotate(Math.PI * 2 / 7);
      ctx.beginPath(); ctx.moveTo(R * .22, 0);
      ctx.quadraticCurveTo(R * .55, -R * .32, R * .86, -R * .12);
      ctx.quadraticCurveTo(R * .6, R * .02, R * .22, R * .12);
      ctx.fillStyle = `rgba(${a[0]},${a[1]},${a[2]},.10)`; ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,.07)'; ctx.lineWidth = 1; ctx.stroke();
    }
    ctx.restore();
    // LED ring
    const m = seg.length || 1, step = Math.PI * 2 / m;
    ctx.lineCap = 'round';
    for (let i = 0; i < seg.length; i++) {
      const c = seg[i], ang = -Math.PI / 2 + i * step;
      ctx.beginPath(); ctx.arc(cx, cy, R, ang + .04, ang + step - .04);
      ctx.strokeStyle = css(c); ctx.lineWidth = s * .065;
      ctx.shadowColor = css(c); ctx.shadowBlur = lum(c) > .02 ? s * .14 : 0;
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
    ctx.beginPath(); ctx.arc(cx, cy, R * .2, 0, 7); ctx.fillStyle = '#101018'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, R * .07, 0, 7); ctx.fillStyle = css(a); ctx.fill();
  }
}
function drawStrip(ctx, W, H, cols) {
  const n = cols.length, gap = n > 40 ? 1 : 3, w = (W - 8 - gap * (n - 1)) / n;
  rr(ctx, 0, 2, W, H - 4, 8); ctx.fillStyle = '#0a0a11'; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.stroke();
  for (let i = 0; i < n; i++) {
    const c = cols[i]; ctx.fillStyle = css(c); ctx.shadowColor = css(c); ctx.shadowBlur = lum(c) > .02 ? 12 : 0;
    rr(ctx, 4 + i * (w + gap), H * .3, Math.max(1, w), H * .4, Math.min(4, w / 2)); ctx.fill();
  }
  ctx.shadowBlur = 0;
}
function drawStick(ctx, W, H, cols) {
  rr(ctx, 1, 1, W - 2, H - 2, 5);
  const g = ctx.createLinearGradient(0, 0, W, 0); g.addColorStop(0, '#16161f'); g.addColorStop(.5, '#22222e'); g.addColorStop(1, '#16161f');
  ctx.fillStyle = g; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.stroke();
  const n = cols.length, top = 8, bh = H * .62, h = bh / n;
  for (let i = 0; i < n; i++) {
    const c = cols[i]; ctx.fillStyle = css(c); ctx.shadowColor = css(c); ctx.shadowBlur = lum(c) > .02 ? 12 : 0;
    rr(ctx, W * .2, top + i * h + .5, W * .6, Math.max(1, h - 1.2), 2); ctx.fill();
  }
  ctx.shadowBlur = 0;
  ctx.fillStyle = 'rgba(255,255,255,.05)';
  for (let i = 0; i < 6; i++) { ctx.fillRect(W * .25, top + bh + 10 + i * 9, W * .5, 3); }
  ctx.fillStyle = '#b08a3c'; for (let i = 0; i < 8; i++) ctx.fillRect(3 + i * (W - 6) / 8, H - 6, (W - 6) / 8 - 1.5, 4);
}
function drawSingle(ctx, W, H, c) {
  const r = Math.min(W, H) / 2 - 6;
  ctx.beginPath(); ctx.arc(W / 2, H / 2, r + 4, 0, 7); ctx.fillStyle = '#0a0a11'; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.stroke();
  ctx.beginPath(); ctx.arc(W / 2, H / 2, r * .7, 0, 7); ctx.fillStyle = css(c); ctx.shadowColor = css(c); ctx.shadowBlur = lum(c) > .02 ? 20 : 0; ctx.fill(); ctx.shadowBlur = 0;
}
function drawMouse(ctx, W, H, dev, bytes) {
  const cx = W / 2;
  const zcol = name => { const zi = dev.zones.findIndex(z => z.name.toLowerCase().includes(name)); return zi < 0 ? null : rgbAt(bytes, zoneOffset(dev, zi)); };
  const all = []; for (let i = 0; i < dev.leds; i++) all.push(rgbAt(bytes, i));
  const a = avg(all);
  const under = dev.zones.find(z => !/logo|scroll|wheel|rad/i.test(z.name));
  const uc = under ? rgbAt(bytes, zoneOffset(dev, dev.zones.indexOf(under))) : null;
  if (uc) { ctx.shadowColor = css(uc); ctx.shadowBlur = 24; }
  ctx.beginPath(); ctx.ellipse(cx, H * .52, W * .4, H * .45, 0, 0, 7); ctx.fillStyle = '#0d0d15'; ctx.fill(); ctx.shadowBlur = 0;
  ctx.strokeStyle = `rgba(${a[0]},${a[1]},${a[2]},.35)`; ctx.lineWidth = 1.2; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx, H * .08); ctx.lineTo(cx, H * .36); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.stroke();
  const sc = zcol('scroll') || zcol('wheel') || zcol('rad');
  if (sc) { rr(ctx, cx - 4, H * .17, 8, H * .14, 4); ctx.fillStyle = css(sc); ctx.shadowColor = css(sc); ctx.shadowBlur = 14; ctx.fill(); ctx.shadowBlur = 0; }
  const lc = zcol('logo') || (dev.zones.length ? rgbAt(bytes, 0) : null);
  if (lc) {
    ctx.save(); ctx.translate(cx, H * .7); ctx.fillStyle = css(lc); ctx.shadowColor = css(lc); ctx.shadowBlur = 18;
    ctx.beginPath(); for (let i = 0; i < 3; i++) { const an = i * 2.094 - 1.57; ctx.moveTo(0, 0); ctx.arc(0, 0, W * .12, an - .45, an + .45); }
    ctx.fill(); ctx.restore(); ctx.shadowBlur = 0;
  }
}
function drawPuck(ctx, W, H, dev, bytes) {
  const all = []; for (let i = 0; i < dev.leds; i++) all.push(rgbAt(bytes, i));
  const c = avg(all);
  ctx.beginPath(); ctx.ellipse(W / 2, H * .62, W * .44, H * .28, 0, 0, 7); ctx.fillStyle = css(c); ctx.shadowColor = css(c); ctx.shadowBlur = lum(c) > .02 ? 26 : 0; ctx.fill(); ctx.shadowBlur = 0;
  ctx.beginPath(); ctx.ellipse(W / 2, H * .5, W * .42, H * .3, 0, 0, 7); ctx.fillStyle = '#101018'; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.stroke();
  ctx.beginPath(); ctx.ellipse(W / 2, H * .46, W * .24, H * .13, 0, 0, 7); ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.stroke();
}

/* ------------------------------------------------------------ frame loop */
let lastAmb = 0, polling = false;
async function poll() {
  if (polling) return; polling = true;
  try {
    const f = await api('frame');
    FRAME = f;
    for (const [k, hex] of Object.entries(f.devices)) {
      if (!hex) { BYTES[k] = null; continue; }
      let a = BYTES[k]; if (!a || a.length !== hex.length / 2) a = BYTES[k] = new Uint8Array(hex.length / 2);
      for (let i = 0; i < a.length; i++) a[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    if (S && f.dv !== S.backend.version) { await refresh(); if (UI.page !== 'settings' && UI.page !== 'schedule') renderPage(); }
    updateChrome();
  } catch (e) { /* backend restarting */ }
  polling = false;
  setTimeout(poll, document.hidden ? 500 : 20);
}
// displayed colours glide towards the latest frame every animation frame -> fluid preview at screen refresh rate
const DISP = {};
let lastRaf = 0;
function glide(t) {
  const k = 1 - Math.pow(0.001, Math.min(0.1, (t - lastRaf) / 1000) * 6);  // ~60% per 16ms frame
  lastRaf = t;
  for (const key in BYTES) {
    const b = BYTES[key];
    if (!b) { DISP[key] = null; continue; }
    let d = DISP[key];
    if (!d || d.length !== b.length) { DISP[key] = Float32Array.from(b); continue; }
    for (let i = 0; i < b.length; i++) d[i] += (b[i] - d[i]) * k;
  }
}
function raf(t) {
  if (document.hidden) { requestAnimationFrame(raf); return; }
  glide(t);
  spin = (t / 1000) * 1.4;
  if (S) drawAll();
  if (t - lastAmb > 400) { lastAmb = t; ambient(); updateSwatches(); }
  requestAnimationFrame(raf);
}
function ambient() {
  const all = [];
  for (const k in BYTES) { const b = BYTES[k]; if (!b) continue; for (let i = 0; i < b.length; i += 3) all.push([b[i], b[i + 1], b[i + 2]]); }
  if (!all.length) return;
  const third = Math.ceil(all.length / 3), root = document.documentElement.style;
  for (let k = 0; k < 3; k++) {
    const part = all.slice(k * third, (k + 1) * third); if (!part.length) continue;
    const a = avg(part), m = Math.max(...a, 1), boost = m < 30 ? 0 : 255 / m;
    root.setProperty('--amb' + (k + 1), `rgb(${a.map(v => Math.round(v * boost * .9)).join(',')})`);
  }
}
function updateChrome() {
  if (!FRAME) return;
  const st = $('#status');
  st.classList.toggle('ok', !FRAME.demo);
  $('.txt', st).textContent = FRAME.demo ? 'Demo-Modus' : FRAME.status.replace('Verbunden mit OpenRGB · ', 'OpenRGB · ');
  st.title = FRAME.status;
  document.body.classList.toggle('off', !FRAME.power);
  const pw = $('#pw'); if (pw) pw.classList.toggle('on', !!FRAME.power);
  const m = $('#perfInfo'); if (m) m.textContent = `${FRAME.fps} FPS · ${FRAME.ms} ms pro Bild · CPU ${Math.round(FRAME.cpu * 100)} %`;
  if (FRAME.scene !== S.settings.active_scene) { S.settings.active_scene = FRAME.scene; if (UI.page === 'scenes') renderPage(); }
}

/* ===================================================================
   SHELL
   =================================================================== */
const PAGES = [['scenes', 'Szenen', 'scenes'], ['studio', 'Studio', 'studio'], ['devices', 'Geräte', 'devices'], ['fans', 'Lüfter', 'fan'], ['schedule', 'Zeitplan', 'schedule'], ['settings', 'Einstellungen', 'settings']];
function renderNav() {
  $('#nav').innerHTML = PAGES.map(([id, label, ic]) => {
    const count = id === 'scenes' ? S.scenes.length : id === 'devices' ? S.devices.length : id === 'schedule' ? (S.schedule.length || '') : '';
    return `<button data-go="${id}" class="${UI.page === id ? 'on' : ''}">${icon(ic)}<span>${label}</span>${count !== '' ? `<span class="count">${count}</span>` : ''}</button>`;
  }).join('');
}
function renderQuick() {
  // sidebar device list (iCUE-style) with a live colour swatch per device
  $('#quick').innerHTML = S.devices.map(d => `<button data-godev="${esc(d.key)}" class="${d.cfg.enabled === false ? 'off' : ''}" title="${esc(devName(d))}">
      ${icon(TYPE_ICON(d.type))}<span class="nm">${esc(devName(d))}</span><span class="sw" data-sw="${esc(d.key)}"></span></button>`).join('')
    || '<div class="dim" style="padding:4px 12px;font-size:12px">Keine Geräte</div>';
}
function updateSwatches() {
  for (const el of $$('[data-sw]')) {
    const b = DISP[el.dataset.sw] || BYTES[el.dataset.sw];
    if (!b || !b.length) { el.style.background = '#2a2a30'; continue; }
    let r = 0, g = 0, bl = 0, n = b.length / 3;
    for (let i = 0; i < b.length; i += 3) { r += b[i]; g += b[i + 1]; bl += b[i + 2]; }
    el.style.background = `rgb(${r / n | 0},${g / n | 0},${bl / n | 0})`;
  }
}
function renderMaster() {
  const s = S.settings;
  $('#master').innerHTML = `
    <div class="row between"><div><div class="lbl">Beleuchtung</div><div class="val">${s.power ? 'An' : 'Aus'}</div></div>
      <button class="power ${s.power ? 'on' : ''}" id="pw" title="Licht an/aus">${icon('power')}</button></div>
    <div><div class="row between"><span class="lbl">Gesamthelligkeit</span><span class="val" id="mval">${s.master}%</span></div>
      <div class="range grad"><input type="range" min="0" max="100" value="${s.master}" id="masterR" style="--p:${s.master}%"></div></div>`;
}
function go(page) { UI.page = page; renderNav(); renderPage(); $('#main').scrollTop = 0; }

async function refresh() {
  S = await api('state');
  renderNav(); renderQuick(); renderMaster();
}
function renderPage() {
  const m = $('#main');
  const fn = { scenes: pageScenes, studio: pageStudio, devices: pageDevices, fans: pageFans, schedule: pageSchedule, settings: pageSettings }[UI.page];
  const enter = UI.lastPage !== UI.page; UI.lastPage = UI.page;
  const top = m.scrollTop;
  m.innerHTML = `<div class="page ${enter ? 'enter' : ''}">${fn()}</div>`;
  if (!enter) m.scrollTop = top;
  setupCanvases(m);
  afterRender();
}
let afterHooks = [];
function afterRender() { const h = afterHooks; afterHooks = []; h.forEach(f => f()); }

/* ===================================================================
   PAGE: SCENES
   =================================================================== */
function pageScenes() {
  const act = scene(S.settings.active_scene) || S.scenes[0];
  const ORDER = ['White Build', 'Gaming', 'Bunt', 'Natur', 'Entspannt', 'Musik', 'Praktisch', 'Eigene'];
  const present = new Set(S.scenes.map(s => s.category || 'Eigene'));
  const cats = ['Alle', 'Favoriten', ...ORDER.filter(c => present.has(c)), ...[...present].filter(c => !ORDER.includes(c))];
  const q = UI.q.toLowerCase();
  const list = S.scenes.filter(s => (UI.cat === 'Alle' || (UI.cat === 'Favoriten' ? S.favorites.includes(s.id) : (s.category || 'Eigene') === UI.cat))
    && (!q || s.name.toLowerCase().includes(q) || (s.desc || '').toLowerCase().includes(q)));
  const cols = act ? sceneColors(act) : [];
  return `
  <div class="page-head"><div><h1>Szenen</h1><p>${S.scenes.length} Lichtstimmungen für alle Geräte</p></div><div class="grow"></div>
    <button class="btn" data-act="import">${icon('upload')} Importieren</button>
    <button class="btn primary" data-act="new-scene">${icon('plus')} Neue Szene</button></div>
  <div class="hero">
    <div class="card stage"><div class="row between"><span class="live-badge"><i></i> Live-Vorschau</span><span class="dim" style="font-size:12px">${S.devices.length} Geräte · ${S.devices.reduce((a, d) => a + d.leds, 0)} LEDs</span></div>${rigHtml(false)}</div>
    <div class="card info">${act ? `
      <span class="tag">${esc(act.category || 'Eigene')}</span>
      <h2>${esc(act.name)}</h2><p>${esc(act.desc || 'Eigene Szene')}</p>
      <div class="palette-bar" style="background:${gradCss(cols, '90deg')};height:8px;border-radius:4px"></div>
      <div class="actions">
        <button class="btn primary" data-edit="${act.id}">${icon('edit')} Bearbeiten</button>
        <button class="btn" data-dup="${act.id}">${icon('copy')} Duplizieren</button>
        <button class="btn icon ${S.favorites.includes(act.id) ? 'primary' : ''}" data-fav="${act.id}" title="Favorit">${icon('star')}</button>
      </div>` : ''}</div>
  </div>
  <div class="filters">${cats.map(c => `<button class="chip ${UI.cat === c ? 'on' : ''}" data-cat="${esc(c)}">${esc(c)}</button>`).join('')}
    <div class="grow"></div><label class="search">${icon('search')}<input id="q" placeholder="Szene suchen …" value="${esc(UI.q)}"></label></div>
  <div class="scene-grid">${list.map(sceneCard).join('')}
    <div class="scene new" data-act="new-scene"><div>${icon('plus')}Neue Szene</div></div></div>`;
}
function sceneCard(sc) {
  const cols = sceneColors(sc), on = S.settings.active_scene === sc.id, fav = S.favorites.includes(sc.id);
  const dur = 6 + (sc.id.length % 5) * 1.7;
  return `<div class="scene ${on ? 'on' : ''}" data-scene="${sc.id}" style="--g:${gradCss([...cols, cols[0]])};--glow:${cols[0]}88;--dur:${dur}s">
    <div class="bg"></div><span class="playing"><span class="eq"><i></i><i></i><i></i></span> AKTIV</span>
    <button class="fav ${fav ? 'on' : ''}" data-fav="${sc.id}" title="Favorit">${icon('star')}</button>
    <div class="meta2"><h3>${esc(sc.name)}</h3><span>${esc(sc.category || 'Eigene')} · ${sc.layers.length} ${sc.layers.length === 1 ? 'Ebene' : 'Ebenen'}</span></div></div>`;
}

/* ===================================================================
   PAGE: STUDIO
   =================================================================== */
function editScene() {
  let sc = scene(UI.editId) || scene(S.settings.active_scene) || S.scenes[0];
  if (sc) UI.editId = sc.id;
  return sc;
}
function pageStudio() {
  const sc = editScene();
  if (!sc) return `<div class="empty">Keine Szene vorhanden.</div>`;
  if (S.settings.active_scene !== sc.id) { api('set_scene', sc.id); S.settings.active_scene = sc.id; }
  let ly = sc.layers.find(l => l.id === UI.layerId) || sc.layers[0];
  UI.layerId = ly ? ly.id : null;
  return `
  <div class="page-head"><div><h1>Studio</h1><p>Baue deine eigene Szene aus Ebenen – alles wirkt sofort live.</p></div><div class="grow"></div>
    <select id="scenePick" style="min-width:220px">${S.scenes.map(s => `<option value="${s.id}" ${s.id === sc.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
  </div>
  <div class="studio">
    <div class="col">
      <div class="card panel scene-title">
        <input type="text" id="scName" value="${esc(sc.name)}" maxlength="40">
        <textarea id="scDesc" rows="2" placeholder="Beschreibung …">${esc(sc.desc || '')}</textarea>
        <div class="row" style="margin-top:8px"><span class="dim" style="font-size:12px">Kategorie</span><input type="text" id="scCat" value="${esc(sc.category || 'Eigene')}" style="flex:1;padding:5px 9px"></div>
      </div>
      <div class="card panel">
        <h4><span class="row" style="gap:8px">Ebenen <span class="info-dot" title="Speziellere Ziele gewinnen: Zone › Gerät › Lüfter › Gerätetyp › Alle. Bei Gleichstand gewinnt die untere Ebene.">i</span></span><button class="btn sm" data-act="add-layer">${icon('plus')} Ebene</button></h4>
        <div class="layer-list">${sc.layers.map(l => layerItem(l)).join('') || '<div class="dim">Noch keine Ebene.</div>'}</div>
      </div>
      <div class="card panel"><div class="row wrap">
        <button class="btn sm" data-dup="${sc.id}">${icon('copy')} Duplizieren</button>
        <button class="btn sm" data-act="export">${icon('download')} Exportieren</button>
        <button class="btn sm danger" data-act="del-scene">${icon('trash')} Löschen</button></div></div>
    </div>
    <div class="col"><div class="card preview-stage"><div class="row between"><span class="live-badge"><i></i> Live</span><span class="dim" style="font-size:12px" id="fpsLbl"></span></div>${rigHtml(false)}</div></div>
    <div class="col">${ly ? inspector(sc, ly) : '<div class="card panel dim">Füge eine Ebene hinzu.</div>'}</div>
  </div>`;
}
function layerItem(l) {
  const ef = effect(l.effect) || { name: l.effect, icon: 'circle' };
  const [, lbl] = targetLabel(l.target);
  const cols = sceneColors({ layers: [l] });
  return `<div class="layer ${l.id === UI.layerId ? 'on' : ''} ${l.enabled === false ? 'disabled' : ''}" data-layer="${l.id}">
    <div class="ic" style="--c:${cols[0]};background:${gradCss(cols.slice(0, 3), '135deg')}">${icon(ef.icon)}</div>
    <div class="tx"><b>${esc(ef.name)}</b><span>${esc(lbl)}${l.span === 'group' ? ' · durchgehend' : ''}</span></div>
    <label class="switch" title="Ebene aktiv"><input type="checkbox" data-lyen="${l.id}" ${l.enabled !== false ? 'checked' : ''}><span></span></label></div>`;
}
function inspector(sc, ly) {
  const tabs = `<div class="tabs-inline"><button class="${UI.tab === 'effect' ? 'on' : ''}" data-tab="effect">Effekt</button><button class="${UI.tab === 'params' ? 'on' : ''}" data-tab="params">Einstellungen</button><button class="${UI.tab === 'target' ? 'on' : ''}" data-tab="target">Ziel</button></div>`;
  let body = '';
  if (UI.tab === 'effect') {
    const cats = [...new Set(S.effects.map(e => e.category))];
    body = cats.map(c => `<div class="fx-cat">${esc(c)}</div><div class="fx-grid">${S.effects.filter(e => e.category === c).map(e => `<button class="fx ${e.id === ly.effect ? 'on' : ''}" data-fx="${e.id}" title="${esc(e.desc)}">${icon(e.icon)}<span>${esc(e.name)}</span></button>`).join('')}</div>`).join('');
  } else if (UI.tab === 'params') {
    const ef = effect(ly.effect);
    const COMMON = ['speed', 'flow', 'reverse', 'brightness'];
    const own = ef.params.filter(p => !COMMON.includes(p.key)), common = ef.params.filter(p => COMMON.includes(p.key));
    body = `<p class="muted" style="margin:0 0 16px;font-size:12.5px"><b style="color:var(--text)">${esc(ef.name)}</b> – ${esc(ef.desc)}${ef.audio ? ' Reagiert auf alles, was über deine Lautsprecher läuft.' : ''}</p>
      <div class="params">${own.map(p => paramHtml(p, ly.params[p.key])).join('')}
        ${common.length ? `<div class="param-group">Bewegung & Helligkeit</div>${common.map(p => paramHtml(p, ly.params[p.key])).join('')}` : ''}</div>
      <div class="row" style="margin-top:18px"><button class="btn sm ghost" data-act="reset-params">${icon('refresh')} Standardwerte</button></div>`;
  } else {
    body = targetEditor(ly);
  }
  return `<div class="card panel">${tabs}${body}</div>`;
}
function targetEditor(ly) {
  const t = ly.target || { type: 'all' };
  const types = [...new Set(S.devices.map(d => d.type))];
  let extra = '';
  if (t.type === 'devtype') extra = `<select data-tgt="dtype">${types.map(x => `<option value="${x}" ${t.dtype === x ? 'selected' : ''}>${esc(TYPE_LABEL[x] || x)}</option>`).join('')}</select>`;
  if (t.type === 'device' || t.type === 'zone') extra = `<select data-tgt="key">${S.devices.map(d => `<option value="${esc(d.key)}" ${t.key === d.key ? 'selected' : ''}>${esc(devName(d))}</option>`).join('')}</select>`;
  if (t.type === 'zone') { const d = device(t.key) || S.devices[0]; extra += `<select data-tgt="zone">${(d ? d.zones : []).map(z => `<option value="${z.index}" ${+t.zone === z.index ? 'selected' : ''}>${esc(z.name)} (${z.leds} LEDs)</option>`).join('')}</select>`; }
  const opts = [['all', 'Alle Geräte'], ['fans', 'Alle Lüfter'], ['devtype', 'Gerätetyp'], ['device', 'Ein Gerät'], ['zone', 'Eine Zone']];
  return `<div class="params">
    <div class="field"><span class="lbl">Wirkt auf</span><div class="seg">${opts.map(([v, l]) => `<button data-ttype="${v}" class="${t.type === v ? 'on' : ''}">${l}</button>`).join('')}</div>${extra}</div>
    <div class="field"><span class="lbl">Verteilung</span><div class="seg"><button data-span="zone" class="${ly.span !== 'group' ? 'on' : ''}">Jede Zone einzeln</button><button data-span="group" class="${ly.span === 'group' ? 'on' : ''}">Durchgehend über alle</button></div>
      <span class="hint">„Durchgehend“ verbindet alle Ziel-LEDs zu einer langen Kette, sodass Wellen von Gerät zu Gerät fließen.</span></div>
    <div class="field"><span class="lbl">Ebene</span><div class="row"><button class="btn sm danger" data-act="del-layer">${icon('trash')} Ebene löschen</button><button class="btn sm" data-act="dup-layer">${icon('copy')} Duplizieren</button></div></div>
  </div>`;
}
function paramHtml(p, v) {
  if (v === undefined) v = p.default;
  const lab = `<label>${esc(p.label)}</label>`;
  switch (p.type) {
    case 'range': {
      const pct = ((v - p.min) / (p.max - p.min)) * 100;
      return `<div class="field">${lab}<div class="range"><input type="range" data-p="${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${v}" style="--p:${pct}%"><output>${fmt(v, p)}</output></div></div>`;
    }
    case 'color': return `<div class="field"><div class="lbl"><span>${esc(p.label)}</span><span class="mono dim">${esc(v)}</span></div><div class="row"><button class="swatch" data-color="${p.key}" style="--c:${v}"></button>${['#ff0040', '#ff7a00', '#ffd600', '#00ff80', '#00e5ff', '#2060ff', '#9b4dff', '#ff2bd6', '#ffffff', '#000000'].map(c => `<button class="swatch" data-setcolor="${p.key}" data-v="${c}" style="--c:${c};width:22px;height:22px;border-radius:7px;border-width:1px"></button>`).join('')}</div></div>`;
    case 'palette': {
      const cols = palColors(v), isPreset = typeof v === 'string';
      return `<div class="field palette-edit"><div class="lbl"><span>${esc(p.label)}</span><span class="dim">${isPreset ? esc(S.palettes[v]?.name || '') : 'Eigene Farben'}</span></div>
        <div class="palette-bar" style="background:${gradCss(cols, '90deg')}"></div>
        <div class="stops">${cols.map((c, i) => `<button class="swatch" data-stop="${p.key}" data-i="${i}" style="--c:${c}">${cols.length > 1 ? `<span class="x" data-rmstop="${p.key}" data-i="${i}">×</span>` : ''}</button>`).join('')}
          ${cols.length < 10 ? `<button class="add" data-addstop="${p.key}" title="Farbe hinzufügen">${icon('plus')}</button>` : ''}</div>
        <details class="pal-more" ${UI.palOpen === p.key ? 'open' : ''} data-palopen="${p.key}"><summary>Vorlagen-Paletten (${Object.keys(S.palettes).length})</summary>
        <div class="pal-presets">${Object.entries(S.palettes).map(([id, pl]) => `<button data-pal="${p.key}" data-v="${id}" class="${v === id ? 'on' : ''}"><i style="background:${gradCss(pl.colors, '90deg')}"></i>${esc(pl.name)}</button>`).join('')}</div></details></div>`;
    }
    case 'select': return `<div class="field">${lab}<div class="seg">${p.options.map(o => `<button data-sel="${p.key}" data-v="${esc(o.value)}" class="${o.value === v ? 'on' : ''}">${esc(o.label)}</button>`).join('')}</div></div>`;
    case 'bool': return `<div class="set-row"><div class="t"><b style="font-size:13px;font-weight:550">${esc(p.label)}</b></div><label class="switch"><input type="checkbox" data-bool="${p.key}" ${v ? 'checked' : ''}><span></span></label></div>`;
  }
  return '';
}
function fmt(v, p) { const n = +v; return (p.step < 1 ? n.toFixed(2).replace(/\.?0+$/, '') : Math.round(n)) + (p.unit || ''); }

let saveT = null;
function curLayer() { const sc = editScene(); return sc && sc.layers.find(l => l.id === UI.layerId); }
function saveScene(rerender = false) {
  clearTimeout(saveT);
  const sc = editScene();
  saveT = setTimeout(() => api('save_scene', sc).catch(e => toast(e.message, true)), 90);
  if (rerender) { renderPage(); renderQuick(); }
}
function setParam(key, val, rerender = false) { const ly = curLayer(); if (!ly) return; ly.params[key] = val; saveScene(rerender); }

/* ===================================================================
   COLOR PICKER POPOVER
   =================================================================== */
function hexToHsv(h) {
  const n = parseInt(h.slice(1), 16), r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let hh = 0; if (d) { hh = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; hh /= 6; if (hh < 0) hh += 1; }
  return [hh, mx ? d / mx : 0, mx];
}
function hsvToHex(h, s, v) {
  const f = n => { const k = (n + h * 6) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return '#' + [f(5), f(3), f(1)].map(x => Math.round(x * 255).toString(16).padStart(2, '0')).join('');
}
let pop = null;
function openPicker(anchor, value, onChange) {
  closePicker();
  let [h, s, v] = hexToHsv(/^#[0-9a-f]{6}$/i.test(value) ? value : '#ffffff');
  const el = document.createElement('div'); el.className = 'pop';
  const presets = ['#ff0000', '#ff4d00', '#ff9900', '#ffd600', '#b6ff00', '#00ff44', '#00ffaa', '#00e5ff', '#0090ff', '#2040ff', '#6a00ff', '#a000ff', '#ff00d4', '#ff0080', '#ffffff', '#ffd9a8', '#808080', '#000000'];
  el.innerHTML = `<div class="sv"><div class="knob"></div></div><div class="hue"><div class="knob"></div></div>
    <div class="row"><span class="swatch" style="width:38px;height:38px"></span><input type="text" class="mono" maxlength="7" style="flex:1"></div>
    <div class="presets">${presets.map(c => `<button data-v="${c}" style="background:${c}"></button>`).join('')}</div>`;
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect();
  el.style.left = clamp(r.left - 120, 10, innerWidth - 290) + 'px';
  el.style.top = (r.bottom + 300 > innerHeight ? r.top - 312 : r.bottom + 8) + 'px';
  const sv = $('.sv', el), hue = $('.hue', el), inp = $('input', el), sw = $('.swatch', el);
  const paint = (emit = true) => {
    const hex = hsvToHex(h, s, v);
    sv.style.background = `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,${hsvToHex(h, 1, 1)})`;
    $('.knob', sv).style.left = s * 100 + '%'; $('.knob', sv).style.top = (1 - v) * 100 + '%';
    $('.knob', hue).style.left = h * 100 + '%';
    sw.style.setProperty('--c', hex); if (document.activeElement !== inp) inp.value = hex;
    if (emit) onChange(hex);
  };
  const drag = (area, fn) => area.addEventListener('pointerdown', e => {
    area.setPointerCapture(e.pointerId);
    const mv = ev => { const b = area.getBoundingClientRect(); fn(clamp((ev.clientX - b.left) / b.width, 0, 1), clamp((ev.clientY - b.top) / b.height, 0, 1)); paint(); };
    mv(e); area.onpointermove = mv; area.onpointerup = () => { area.onpointermove = null; };
  });
  drag(sv, (x, y) => { s = x; v = 1 - y; });
  drag(hue, x => { h = Math.min(.999, x); });
  inp.addEventListener('input', () => { if (/^#[0-9a-f]{6}$/i.test(inp.value)) { [h, s, v] = hexToHsv(inp.value); paint(); } });
  el.addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (b) { [h, s, v] = hexToHsv(b.dataset.v); paint(); } });
  paint(false);
  pop = el;
  setTimeout(() => document.addEventListener('pointerdown', outside), 0);
}
function outside(e) { if (pop && !pop.contains(e.target)) closePicker(); }
function closePicker() { if (pop) { pop.remove(); pop = null; document.removeEventListener('pointerdown', outside); } }

/* ===================================================================
   PAGE: DEVICES
   =================================================================== */
let conflictCache = [];
function pageDevices() {
  afterHooks.push(async () => { conflictCache = await api('conflicts').catch(() => []); const b = $('#conflicts'); if (b) b.innerHTML = conflictBanner(); });
  return `
  <div class="page-head"><div><h1>Geräte</h1><p>Lüfter, Arbeitsspeicher, Maus & Co. – Anordnung, LED-Anzahl und Steuerung.</p></div><div class="grow"></div>
    <button class="btn" data-act="rescan">${icon('refresh')} Neu scannen</button></div>
  ${S.backend.demo ? `<div class="banner info">${icon('info')}<div class="grow"><b>Demo-Modus – deine Hardware als Nachbildung</b><span>Prisma steuert die echten Geräte über den OpenRGB-Dienst. Sobald er läuft, erscheinen hier deine echten Geräte.</span></div><button class="btn primary" data-go="settings">${icon('plug')} Einrichten</button></div>` : ''}
  <div id="conflicts">${conflictBanner()}</div>
  <div class="dev-grid">${S.devices.map(deviceCard).join('')}</div>`;
}
function conflictBanner() {
  if (!conflictCache.length) return '';
  return `<div class="banner">${icon('warn')}<div class="grow"><b>Andere RGB-Programme laufen gerade</b><span>${conflictCache.map(c => esc(c.label)).join(', ')} – diese können sich mit Prisma um die LEDs streiten und Flackern verursachen.</span></div><button class="btn" data-act="kill-conflicts">Jetzt beenden</button></div>`;
}
function deviceCard(d) {
  const cfg = d.cfg, ctl = cfg.control || 'prisma';
  const mode = ctl.startsWith('hw:') ? 'hw' : ctl;
  return `<div class="card dev" data-dev="${esc(d.key)}">
    <div class="dev-head"><div class="ic">${icon(TYPE_ICON(d.type))}</div>
      <div class="grow"><h3>${esc(devName(d))}</h3><div class="sub">${esc(d.vendor || '')}${d.vendor ? ' · ' : ''}${esc(TYPE_LABEL[d.type] || d.type)} · ${d.leds} LEDs · ${d.zones.length} ${d.zones.length === 1 ? 'Zone' : 'Zonen'}</div></div>
      <button class="btn sm" data-ident="${esc(d.key)}" title="Gerät kurz blinken lassen">${icon('eye')} Finden</button>
      <label class="switch" title="Gerät aktiv"><input type="checkbox" data-dev-en ${cfg.enabled !== false ? 'checked' : ''}><span></span></label></div>
    <div class="dev-body">
      <div class="dev-preview">${rigHtml(true, d.key)}</div>
      <div class="field"><span class="lbl">Steuerung</span><div class="row wrap"><div class="seg">
        <button data-ctl="prisma" class="${mode === 'prisma' ? 'on' : ''}">Prisma-Effekte</button>
        <button data-ctl="hw" class="${mode === 'hw' ? 'on' : ''}" ${d.modes.length ? '' : 'disabled'}>Hardware-Effekt</button>
        <button data-ctl="off" class="${mode === 'off' ? 'on' : ''}">Aus</button></div>
        ${mode === 'hw' ? `<select data-hwmode>${d.modes.filter(m => m.toLowerCase() !== 'direct').map(m => `<option ${ctl === 'hw:' + m ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select>` : ''}</div>
        ${mode === 'hw' ? '<span class="hint">Der Effekt läuft direkt im Gerät – spart CPU, aber ohne Prisma-Szenen.</span>' : ''}</div>
      <div class="field"><div class="lbl"><span>Helligkeit dieses Geräts</span><span class="dim">über 100 % = Boost für dunkle Szenen</span></div><div class="range"><input type="range" min="0" max="200" value="${cfg.brightness}" data-dev-bri style="--p:${cfg.brightness / 2}%"><output>${cfg.brightness}%</output></div></div>
      ${/wireless|lightspeed|hyperspeed|slipstream/i.test(d.name) ? `<div class="note">Funkgerät: max. 25 FPS, Farben werden jede Sekunde aufgefrischt. Die Grundhelligkeit stellst du am Gerät selbst ein (Helligkeitstaste).</div>` : ''}
      ${d.zones.map(z => zoneRow(d, z)).join('')}
    </div></div>`;
}
function zoneRow(d, z) {
  const zc = d.cfg.zones[z.index] || {};
  const lay = zc.layout || 'strip';
  const open = UI.zoneOpen && UI.zoneOpen.has(d.key + '#' + z.index);
  const summary = z.leds === 0 ? (z.resizable ? 'Nicht eingerichtet – LED-Assistent starten' : 'Keine LEDs')
    : z.leds === 1 ? 'Einzelne LED'
    : lay === 'fans' ? `${z.leds} LEDs · ${zc.fans || 1} ${(zc.fans || 1) === 1 ? 'Lüfter' : 'Lüfter'}` : `${z.leds} LEDs · Leiste`;
  const canEdit = z.leds > 1 || z.resizable;
  return `<div class="zone-row" data-zone="${z.index}">
    <div><h5>${esc(z.name)}</h5><span class="dim" style="font-size:12px">${summary}${zc.reverse ? ' · umgekehrt' : ''}</span></div>
    <div class="row">${z.resizable ? `<button class="btn sm ${z.leds === 0 ? 'primary' : ''}" data-wizard="${z.index}">${icon('sparkles')} Assistent</button>` : ''}<button class="btn sm ghost" data-identz="${z.index}" title="Blinken lassen">${icon('eye')}</button>${canEdit ? `<button class="btn sm ghost" data-zopen="${z.index}">${open ? 'Schließen' : 'Anpassen'}</button>` : ''}<label class="switch" title="Zone aktiv"><input type="checkbox" data-zen ${zc.enabled !== false ? 'checked' : ''}><span></span></label></div>
    ${open ? `<div class="opts">
      ${z.resizable ? `<div class="field"><span class="lbl">LED-Anzahl</span><div class="stepper"><button data-zsize="-1">−</button><input type="number" data-zsizev value="${z.leds}" min="${z.min ?? 0}" max="${z.max ?? 1024}"><button data-zsize="1">+</button></div></div>` : ''}
      ${z.leds > 1 ? `<div class="field"><span class="lbl">Bauform</span><div class="seg"><button data-zlay="fans" class="${lay === 'fans' ? 'on' : ''}">Lüfter</button><button data-zlay="strip" class="${lay === 'strip' ? 'on' : ''}">Leiste</button></div></div>` : ''}
      ${lay === 'fans' && z.leds > 1 ? `<div class="field"><span class="lbl">Anzahl Lüfter</span><div class="stepper"><button data-zfans="-1">−</button><input type="number" data-zfansv value="${zc.fans || 1}" min="1" max="12"><button data-zfans="1">+</button></div><span class="hint">${Math.round(z.leds / Math.max(1, zc.fans || 1) * 10) / 10} LEDs je Lüfter</span></div>` : ''}
      ${z.leds > 1 ? `<div class="field"><span class="lbl">Startpunkt</span><div class="range"><input type="range" min="0" max="100" value="${zc.offset || 0}" data-zoff style="--p:${zc.offset || 0}%"><output>${zc.offset || 0}%</output></div></div>
      <div class="set-row" style="align-self:end;padding-bottom:6px"><span class="lbl muted" style="font-size:12px;font-weight:600">Richtung umkehren</span><label class="switch"><input type="checkbox" data-zrev ${zc.reverse ? 'checked' : ''}><span></span></label></div>` : ''}
    </div>` : ''}</div>`;
}

/* ------------------------------------------------------------ LED wizard */
let WZ = null;
async function wizardOpen(key, zone) {
  conflictCache = await api('conflicts').catch(() => []);
  WZ = { key, zone, step: 0, fans: 1, k: 8, total: 0 };
  wizardRender();
}
function wizardRender() {
  let bg = $('#wizard');
  if (!WZ) { if (bg) bg.remove(); return; }
  if (!bg) { bg = document.createElement('div'); bg.id = 'wizard'; bg.className = 'modal-bg'; document.body.appendChild(bg); }
  const dv = device(WZ.key), z = dv && dv.zones[WZ.zone];
  const dots = [0, 1, 2, 3].map(i => `<i class="${i <= WZ.step ? 'on' : ''}"></i>`).join('');
  let body = '';
  if (WZ.step === 0) {
    body = `<p class="muted">Wir finden heraus, welche Lüfter an <b>${esc(z ? z.name : '')}</b> hängen und wie viele LEDs sie haben. Schau dabei in dein Gehäuse.</p>
      ${conflictCache.length ? `<div class="banner" style="margin:0">${icon('warn')}<div class="grow"><b>Erst ${esc(conflictCache.map(c => c.label).join(', '))} beenden</b><span>Sonst überschreibt es die Test-Farben und der Assistent zeigt Unsinn.</span></div><button class="btn" data-wz="kill">Beenden</button></div>` : '<div class="note">Kein anderes RGB-Programm stört – los geht’s.</div>'}
      <div class="row between"><button class="btn ghost" data-wz="cancel">Abbrechen</button><button class="btn primary" data-wz="start" ${conflictCache.length ? 'disabled' : ''}>Start</button></div>`;
  } else if (WZ.step === 1) {
    body = `<p class="muted">Alle LEDs an <b>${esc(z.name)}</b> blinken jetzt <b>weiß</b>. Wie viele Lüfter blinken?</p>
      <div class="wz-big"><button class="btn icon" data-wz="fans-">−</button><b>${WZ.fans}</b><button class="btn icon" data-wz="fans+">+</button></div>
      <div class="row between"><button class="btn ghost" data-wz="none">Nichts blinkt</button><button class="btn primary" data-wz="next1">Weiter</button></div>`;
  } else if (WZ.step === 2) {
    body = `<p class="muted">Die LEDs füllen sich <b style="color:#3cb4ff">blau</b>, die nächste LED ist <b style="color:#ff4d6d">rot</b>.<br>Erhöhe, bis <b>alle ${WZ.fans} Lüfter komplett blau</b> sind und <b>kein roter Punkt</b> mehr zu sehen ist – dann eine Stufe zurück, bis der rote Punkt gerade verschwunden ist.</p>
      <div class="wz-big"><button class="btn" data-wz="k-5">−5</button><button class="btn icon" data-wz="k-">−</button><b>${WZ.k}</b><button class="btn icon" data-wz="k+">+</button><button class="btn" data-wz="k+5">+5</button></div>
      <div class="range"><input type="range" min="1" max="240" value="${WZ.k}" data-wzk style="--p:${(WZ.k - 1) / 239 * 100}%"><output>${WZ.k} LEDs</output></div>
      <div class="hint">Typisch: 8–24 LEDs pro Lüfter. Bei ${WZ.fans} Lüftern also etwa ${WZ.fans * 8}–${WZ.fans * 24}.</div>
      <div class="row between"><button class="btn ghost" data-wz="back">Zurück</button><button class="btn primary" data-wz="next2">Alle komplett blau</button></div>`;
  } else {
    const per = Math.round(WZ.k / WZ.fans * 10) / 10;
    body = `<p class="muted"><b>${WZ.k}</b> LEDs an ${esc(z.name)}, verteilt auf <b>${WZ.fans}</b> Lüfter (≈ ${per} LEDs je Lüfter).</p>
      <div class="note">Gleich leuchtet jeder Lüfter testweise in einer eigenen Farbe. Wenn nicht alles stimmt, kannst du die Werte jederzeit unter Geräte anpassen.</div>
      <div class="row between"><button class="btn ghost" data-wz="back">Zurück</button><button class="btn primary" data-wz="save">${icon('check')} Speichern</button></div>`;
  }
  bg.innerHTML = `<div class="modal"><div class="row between"><h3>LED-Assistent</h3><div class="wz-dots">${dots}</div></div>${body}</div>`;
}
async function wizardAction(a) {
  const w = WZ; if (!w) return;
  switch (a) {
    case 'cancel': WZ = null; wizardRender(); if (w.step > 0) { await api('probe_cancel'); await refresh(); renderPage(); } return;
    case 'kill': await api('kill_conflicts', conflictCache.map(c => c.name)); conflictCache = await api('conflicts'); return wizardRender();
    case 'start': await api('probe_start', w.key, w.zone); w.step = 1; return wizardRender();
    case 'fans-': w.fans = Math.max(1, w.fans - 1); return wizardRender();
    case 'fans+': w.fans = Math.min(12, w.fans + 1); return wizardRender();
    case 'none': WZ = null; wizardRender(); await api('probe_finish', w.key, w.zone, 0, 0); await refresh(); renderPage(); return toast('Anschluss als unbenutzt gespeichert');
    case 'next1': w.step = 2; w.k = w.fans * 8; await api('probe_set', 'count', w.k); return wizardRender();
    case 'k-': case 'k+': case 'k-5': case 'k+5': {
      const step = { 'k-': -1, 'k+': 1, 'k-5': -5, 'k+5': 5 }[a];
      w.k = clamp(w.k + step, 1, 240); await api('probe_set', 'count', w.k); return wizardRender();
    }
    case 'back': w.step -= 1; await api('probe_set', w.step === 1 ? 'all' : 'count', w.k); return wizardRender();
    case 'next2': w.step = 3; return wizardRender();
    case 'save': {
      WZ = null; wizardRender();
      await api('probe_finish', w.key, w.zone, w.k, w.fans);
      // show each fan in its own colour so the user can verify the split
      if (scene('cascade')) await api('set_scene', 'cascade');
      await refresh();
      renderPage();
      toast(`Gespeichert: ${w.k} LEDs auf ${w.fans} Lüfter – jeder Lüfter zeigt jetzt eine eigene Farbe`);
      return;
    }
  }
}
document.addEventListener('click', e => {
  const zo = e.target.closest('[data-zopen]');
  if (zo) {
    UI.zoneOpen = UI.zoneOpen || new Set();
    const id = zo.closest('[data-dev]').dataset.dev + '#' + zo.dataset.zopen;
    UI.zoneOpen.has(id) ? UI.zoneOpen.delete(id) : UI.zoneOpen.add(id);
    return renderPage();
  }
  const gd = e.target.closest('[data-godev]');
  if (gd) {
    if (UI.page !== 'devices') go('devices');
    const card = $$('.dev[data-dev]').find(c => c.dataset.dev === gd.dataset.godev);
    if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); card.animate([{ borderColor: 'var(--accent)' }, { borderColor: 'var(--line)' }], 1200); }
    return;
  }
  const b = e.target.closest('[data-wz]'); if (b) { wizardAction(b.dataset.wz); return; }
  const o = e.target.closest('[data-wizard]'); if (o) wizardOpen(o.closest('[data-dev]').dataset.dev, +o.dataset.wizard);
});
document.addEventListener('input', e => {
  if (e.target.dataset && e.target.dataset.wzk !== undefined && WZ) {
    WZ.k = +e.target.value; e.target.nextElementSibling.textContent = WZ.k + ' LEDs';
    e.target.style.setProperty('--p', ((WZ.k - 1) / 239 * 100) + '%');
    throttle('wzk', () => api('probe_set', 'count', WZ ? WZ.k : 1), 50);
    const big = $('#wizard .wz-big b'); if (big) big.textContent = WZ.k;
  }
});

/* ===================================================================
   PAGE: SCHEDULE
   =================================================================== */
function pageSchedule() {
  const list = S.schedule;
  return `
  <div class="page-head"><div><h1>Zeitplan</h1><p>Wechsle Szenen automatisch – z. B. abends warmes Licht, nachts aus.</p></div><div class="grow"></div>
    <button class="btn" data-act="sched-templates">${icon('sparkles')} Vorlagen</button>
    <button class="btn primary" data-act="sched-add">${icon('plus')} Eintrag</button></div>
  <div class="sched">${list.length ? list.map((e, i) => schedItem(e, i)).join('') : `<div class="card empty">${icon('schedule')}<div>Noch keine Einträge. Lege einen an oder nimm eine Vorlage.</div></div>`}</div>`;
}
function schedItem(e, i) {
  const days = e.days || [0, 1, 2, 3, 4, 5, 6];
  const act = e.action || 'scene';
  return `<div class="card sched-item" data-si="${i}">
    <label class="switch"><input type="checkbox" data-sen ${e.enabled !== false ? 'checked' : ''}><span></span></label>
    <input type="time" data-stime value="${esc(e.time || '20:00')}">
    <div class="row wrap">
      <select data-sact><option value="scene" ${act === 'scene' ? 'selected' : ''}>Szene wechseln</option><option value="brightness" ${act === 'brightness' ? 'selected' : ''}>Helligkeit setzen</option><option value="off" ${act === 'off' ? 'selected' : ''}>Licht aus</option><option value="on" ${act === 'on' ? 'selected' : ''}>Licht an</option><option value="fanprofile" ${act === 'fanprofile' ? 'selected' : ''}>Lüfterprofil</option></select>
      ${act === 'fanprofile' ? `<select data-sfan>${[['silent', 'Leise'], ['normal', 'Normal'], ['gaming', 'Gaming'], ['max', 'Volle Leistung']].map(([k, l]) => `<option value="${k}" ${e.profile === k ? 'selected' : ''}>${l}</option>`).join('')}</select>` : ''}
      ${act === 'scene' ? `<select data-sscene>${S.scenes.map(s => `<option value="${s.id}" ${e.scene === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>` : ''}
      ${act === 'brightness' ? `<div class="range" style="min-width:200px"><input type="range" min="0" max="100" value="${e.value ?? 50}" data-sval style="--p:${e.value ?? 50}%"><output>${e.value ?? 50}%</output></div>` : ''}
    </div>
    <div class="days">${DAYS.map((d, k) => `<button data-sday="${k}" class="${days.includes(k) ? 'on' : ''}">${d}</button>`).join('')}</div>
    <button class="btn icon ghost danger" data-sdel>${icon('trash')}</button></div>`;
}
function saveSchedule(rerender = true) { api('set_schedule', S.schedule).catch(e => toast(e.message, true)); if (rerender) { renderPage(); renderNav(); } }

/* ===================================================================
   PAGE: SETTINGS
   =================================================================== */
function pageSettings() {
  const s = S.settings, demo = S.backend.demo;
  const sw = (key, title, sub) => `<div class="set-row"><div class="t"><b>${title}</b><span>${sub}</span></div><label class="switch"><input type="checkbox" data-set="${key}" ${s[key] ? 'checked' : ''}><span></span></label></div>`;
  const rg = (key, title, min, max, step, unit, v) => `<div class="field"><div class="lbl"><span>${title}</span></div><div class="range"><input type="range" data-setr="${key}" min="${min}" max="${max}" step="${step}" value="${v ?? s[key]}" style="--p:${((v ?? s[key]) - min) / (max - min) * 100}%" data-unit="${unit}"><output>${fmtS(v ?? s[key], step, unit)}</output></div></div>`;
  return `
  <div class="page-head"><div><h1>Einstellungen</h1><p>Verbindung, Farbkorrektur, Leistung und Verhalten der App.</p></div></div>
  <div class="set-grid">
    <div class="card panel"><h4>Hardware-Verbindung (OpenRGB)</h4>
      <div class="conn ${demo ? '' : 'ok'}"><span class="big"></span><div class="grow"><b>${demo ? 'Nicht verbunden' : 'Verbunden'}</b><div class="dim" style="font-size:12px">${esc(S.backend.status)}</div></div>
        <button class="btn sm" data-act="reconnect">${icon('refresh')} Verbinden</button></div>
      <div class="field"><span class="lbl">Pfad zu OpenRGB.exe</span><div class="row"><input type="text" id="orgbPath" value="${esc(s.openrgb_path)}" placeholder="z. B. C:\\OpenRGB\\OpenRGB.exe" style="flex:1">
        <button class="btn sm" data-act="find-orgb" title="Automatisch suchen">${icon('search')}</button><button class="btn sm web-hide" data-act="pick-orgb" title="Auswählen">${icon('folder')}</button></div></div>
      <div class="row"><button class="btn primary" data-act="launch-orgb" ${s.openrgb_path ? '' : 'disabled'}>${icon('play')} OpenRGB-Dienst starten</button></div>
      <details class="adv"><summary>Erweitert</summary><div>
      ${sw('openrgb_autostart', 'Dienst automatisch starten', 'Startet OpenRGB unsichtbar im Hintergrund, wenn Prisma startet.')}
      ${sw('openrgb_admin', 'Als Administrator starten', 'Nötig für RAM und Mainboard. Läuft über die Windows-Aufgabe „Prisma\\OpenRGB“ ohne Abfrage.')}
      <div class="row"><div class="field grow"><span class="lbl">Host</span><input type="text" data-sett="openrgb_host" value="${esc(s.openrgb_host)}"></div><div class="field" style="width:110px"><span class="lbl">Port</span><input type="number" data-sett="openrgb_port" value="${s.openrgb_port || ''}" placeholder="Standard"></div></div>
      </div></details>
      ${demo ? `<div class="note"><b>So verbindest du deine echte Hardware:</b><ol class="steps">
        <li>OpenRGB herunterladen von <b>openrgb.org</b> (kostenlos, Open Source) und den Ordner entpacken, z. B. nach <span class="mono">C:\\OpenRGB</span>.</li>
        <li>Oben den Pfad setzen (🔍 sucht automatisch) und „OpenRGB-Dienst starten“.</li>
        <li><b>RGB Fusion, iCUE und Synapse beenden</b>, damit sie sich nicht mit Prisma streiten.</li>
        <li>Unter <b>Geräte</b> die LED-Anzahl deiner Lüfteranschlüsse (D_LED) einstellen.</li></ol></div>` : ''}
    </div>
    <div class="card panel"><h4>Farbkorrektur</h4>
      ${rg('gamma', 'Gamma (Kontrast dunkler Töne)', .5, 2.5, .05, '')}
      ${rg('saturation', 'Sättigung', 0, 150, 1, '%')}
      <div class="field"><span class="lbl">Weißabgleich</span>
        ${['r', 'g', 'b'].map((c, i) => `<div class="range"><span style="width:14px;color:${['#ff5c7a', '#5cff9a', '#5cb6ff'][i]};font-weight:700">${c.toUpperCase()}</span><input type="range" data-setr="wb_${c}" min="40" max="100" step="1" value="${s['wb_' + c]}" style="--p:${(s['wb_' + c] - 40) / 60 * 100}%;--fill:${['#ff5c7a', '#5cff9a', '#5cb6ff'][i]}" data-unit="%"><output>${s['wb_' + c]}%</output></div>`).join('')}
        <span class="hint">Wenn Weiß bläulich wirkt, Blau etwas senken.</span></div>
      <div class="row"><button class="btn sm ghost" data-act="reset-color">${icon('refresh')} Zurücksetzen</button></div>
    </div>
    <div class="card panel"><h4>Leistung & Übergänge</h4>
      <div class="field"><span class="lbl">Bildrate</span><div class="seg">${[30, 45, 60].map(f => `<button data-fps="${f}" class="${+s.fps === f ? 'on' : ''}">${f} FPS</button>`).join('')}</div><span class="hint">60 FPS = am flüssigsten. RAM wird automatisch auf 40, Funkgeräte auf 25 FPS begrenzt.</span></div>
      ${rg('smoothing', 'Bewegungsglättung', 0, 80, 1, '%')}
      <span class="hint" id="perfInfo" style="margin-top:-8px"></span>
      ${rg('transition_ms', 'Überblendzeit beim Szenenwechsel', 0, 3000, 50, ' ms')}
      ${rg('audio_gain', 'Musik-Empfindlichkeit', 20, 300, 5, '%')}
      ${S.audio.error ? `<div class="note warn">${esc(S.audio.error)}</div>` : ''}
    </div>
    <div class="card panel"><h4>Home Assistant</h4>
      <div class="conn ${S.ha && S.ha.connected ? 'ok' : ''}" id="haConn"><span class="big"></span><div class="grow"><b>${S.ha && S.ha.connected ? 'Verbunden' : 'Nicht verbunden'}</b><div class="dim" style="font-size:12px">${esc(S.ha ? S.ha.status : '')}</div></div></div>
      ${sw('mqtt_enabled', 'Mit Home Assistant verbinden', 'Über MQTT. In Home Assistant erscheinen automatisch „PC-Licht“ und „PC-Szene“.')}
      <div class="row"><div class="field grow"><span class="lbl">MQTT-Server</span><input type="text" data-sett="mqtt_host" value="${esc(s.mqtt_host || '')}" placeholder="z. B. homeassistant.local"></div><div class="field" style="width:100px"><span class="lbl">Port</span><input type="number" data-sett="mqtt_port" value="${s.mqtt_port || ''}" placeholder="Standard"></div></div>
      <div class="row"><div class="field grow"><span class="lbl">Benutzer</span><input type="text" data-sett="mqtt_user" value="${esc(s.mqtt_user)}" autocomplete="off"></div><div class="field grow"><span class="lbl">Passwort</span><input type="password" data-sett="mqtt_pass" value="${s.mqtt_pass ? '••••••••' : ''}" autocomplete="new-password" data-secret></div></div>
      ${S.ha && S.ha.connected ? `<div class="note">In Home Assistant: <span class="mono">${esc(S.ha.light)}</span> und <span class="mono">${esc(S.ha.select)}</span></div>` : '<span class="hint">Tipp: In Home Assistant unter Einstellungen → Personen einen eigenen Benutzer „prisma“ anlegen und hier eintragen.</span>'}
    </div>
    <div class="card panel"><h4>Updates</h4>${updatePanel()}
      ${sw('auto_update', 'Updates automatisch installieren', 'Prisma prüft alle 6 Stunden auf GitHub und aktualisiert sich selbst. Einstellungen und Szenen bleiben erhalten.')}
    </div>
    <div class="card panel"><h4>App-Verhalten</h4>
      <div class="set-row"><div class="t"><b>Mit Windows starten</b><span>Prisma startet minimiert im Infobereich.</span></div><label class="switch"><input type="checkbox" data-act-autostart ${S.autostart ? 'checked' : ''}><span></span></label></div>
      ${sw('tray', 'Beim Schließen im Infobereich bleiben', 'Das Licht läuft weiter, auch wenn das Fenster zu ist.')}
      ${sw('start_minimized', 'Minimiert starten', 'Fenster beim Start nicht anzeigen.')}
      <hr class="sep">
      <div class="row wrap"><button class="btn" data-act="reset-scenes">${icon('refresh')} Standard-Szenen wiederherstellen</button>
        <button class="btn danger web-hide" data-act="quit">${icon('power')} Prisma beenden</button></div>
      <div class="dim" style="font-size:12px">Prisma ${esc(S.version || '')} · Daten in <span class="mono">%USERPROFILE%\\.prisma</span></div>
    </div>
  </div>`;
}
function updatePanel() {
  const u = S.update || {};
  const state = u.installing ? 'Update wird installiert – Prisma startet gleich neu …'
    : u.checking ? 'Suche nach Updates …'
    : u.available ? `Version ${esc(u.latest)} ist verfügbar`
    : u.error ? esc(u.error)
    : u.latest ? 'Du hast die neueste Version' : 'Noch nicht geprüft';
  return `<div class="upd ${u.available ? 'new' : ''}"><div class="grow"><b>Prisma ${esc(S.version)}</b><div class="dim" style="font-size:12px">${state}</div></div>
    ${u.available && !u.installing ? `<button class="btn primary sm" data-act="upd-install">${icon('download')} Installieren</button>` : `<button class="btn sm" data-act="upd-check" ${u.checking || u.installing ? 'disabled' : ''}>${icon('refresh')} Prüfen</button>`}</div>
    ${u.available && u.notes ? `<div class="note" style="white-space:pre-wrap;max-height:140px;overflow:auto">${esc(u.notes)}</div>` : ''}`;
}
function fmtS(v, step, unit) { return (step < 1 ? (+v).toFixed(2) : Math.round(v)) + unit; }

/* ===================================================================
   EVENTS
   =================================================================== */
document.addEventListener('click', async e => {
  const t = e.target.closest('[data-go],[data-scene],[data-fav],[data-cat],[data-act],[data-edit],[data-dup],[data-layer],[data-tab],[data-fx],[data-sel],[data-pal],[data-color],[data-setcolor],[data-stop],[data-addstop],[data-rmstop],[data-ttype],[data-span],[data-ctl],[data-ident],[data-identz],[data-zlay],[data-zsize],[data-zfans],[data-fps],[data-sday],[data-sdel],[data-win],#pw');
  if (!t) return;
  const d = t.dataset;
  // --- remove palette stop (inside a swatch)
  if (e.target.closest('[data-rmstop]')) {
    const x = e.target.closest('[data-rmstop]'), ly = curLayer(); const cols = [...palColors(ly.params[x.dataset.rmstop])]; cols.splice(+x.dataset.i, 1);
    return setParam(x.dataset.rmstop, cols, true);
  }
  if (d.win) { if (d.win === 'close') return api('win', 'close'); return api('win', d.win); }
  if (t.id === 'pw') { const on = !S.settings.power; S.settings.power = on; renderMaster(); updateChrome(); return api('set_power', on); }
  if (d.go) return go(d.go);
  if (d.fav !== undefined && t.matches('[data-fav]')) { e.stopPropagation(); const on = !S.favorites.includes(d.fav); S.favorites = await api('set_favorite', d.fav, on); renderQuick(); if (UI.page === 'scenes') renderPage(); return; }
  if (d.scene && t.matches('[data-scene]')) { S.settings.active_scene = d.scene; S.settings.power = true; await api('set_scene', d.scene); renderQuick(); renderMaster(); if (UI.page === 'scenes') renderPage(); return; }
  if (d.cat) { UI.cat = d.cat; return renderPage(); }
  if (d.edit) { UI.editId = d.edit; UI.layerId = null; UI.tab = 'effect'; return go('studio'); }
  if (d.dup) { const s = await api('duplicate_scene', d.dup); await refresh(); UI.editId = s.id; UI.layerId = null; toast('Szene dupliziert'); return go('studio'); }
  if (d.layer && !e.target.closest('.switch')) { UI.layerId = d.layer; return renderPage(); }
  if (d.tab) { UI.tab = d.tab; return renderPage(); }
  if (d.fx) {
    const ly = curLayer(); if (!ly || ly.effect === d.fx) return;
    const def = await api('effect_defaults', d.fx);
    const keep = {}; for (const k of ['brightness', 'speed', 'palette', 'flow', 'reverse']) if (k in def && k in ly.params) keep[k] = ly.params[k];
    ly.effect = d.fx; ly.params = { ...def, ...keep };
    return saveScene(true);
  }
  if (d.sel) return setParam(d.sel, coerce(d.v), true);
  if (d.pal) return setParam(d.pal, d.v, true);
  if (d.setcolor) return setParam(d.setcolor, d.v, true);
  if (d.color) { const ly = curLayer(); return openPicker(t, ly.params[d.color], hex => { ly.params[d.color] = hex; t.style.setProperty('--c', hex); saveScene(); }); }
  if (d.stop !== undefined && t.matches('[data-stop]')) {
    const ly = curLayer(), key = d.stop, i = +d.i;
    return openPicker(t, palColors(ly.params[key])[i], hex => { const cols = [...palColors(ly.params[key])]; cols[i] = hex; ly.params[key] = cols; t.style.setProperty('--c', hex); const bar = t.closest('.palette-edit').querySelector('.palette-bar'); bar.style.background = gradCss(cols, '90deg'); saveScene(); });
  }
  if (d.addstop) { const ly = curLayer(); const cols = [...palColors(ly.params[d.addstop])]; cols.push(cols[cols.length - 1] || '#ffffff'); return setParam(d.addstop, cols, true); }
  if (d.ttype) {
    const ly = curLayer(); const t0 = { type: d.ttype };
    if (d.ttype === 'devtype') t0.dtype = S.devices[0]?.type || 'motherboard';
    if (d.ttype === 'device' || d.ttype === 'zone') t0.key = (ly.target && ly.target.key) || S.devices[0]?.key;
    if (d.ttype === 'zone') t0.zone = 0;
    ly.target = t0; return saveScene(true);
  }
  if (d.span) { curLayer().span = d.span; return saveScene(true); }
  if (d.ctl) {
    const card = t.closest('[data-dev]'), dv = device(card.dataset.dev);
    let ctl = d.ctl; if (ctl === 'hw') ctl = 'hw:' + (dv.modes.find(m => m.toLowerCase() !== 'direct') || '');
    dv.cfg.control = ctl; await api('set_device', dv.key, { control: ctl }); return renderPage();
  }
  if (d.ident) return api('identify', d.ident);
  if (d.identz !== undefined) { const key = t.closest('[data-dev]').dataset.dev; return api('identify', key, +d.identz); }
  if (d.zlay) { const { dv, zi } = zoneCtx(t); dv.cfg.zones[zi].layout = d.zlay; await api('set_zone', dv.key, zi, { layout: d.zlay }); return renderPage(); }
  if (d.zfans) { const { dv, zi } = zoneCtx(t); const zc = dv.cfg.zones[zi]; zc.fans = clamp((zc.fans || 1) + +d.zfans, 1, 12); await api('set_zone', dv.key, zi, { fans: zc.fans }); return renderPage(); }
  if (d.zsize) { const { dv, zi } = zoneCtx(t); const z = dv.zones[zi]; const n = clamp(z.leds + +d.zsize, z.min ?? 0, z.max ?? 1024); await api('resize_zone', dv.key, zi, n); await refresh(); return renderPage(); }
  if (d.fps) { S.settings.fps = +d.fps; await api('set_settings', { fps: +d.fps }); return renderPage(); }
  if (d.sday !== undefined) { const i = +t.closest('[data-si]').dataset.si, e2 = S.schedule[i]; const days = new Set(e2.days || [0, 1, 2, 3, 4, 5, 6]); days.has(+d.sday) ? days.delete(+d.sday) : days.add(+d.sday); e2.days = [...days].sort(); return saveSchedule(); }
  if (d.sdel !== undefined) { S.schedule.splice(+t.closest('[data-si]').dataset.si, 1); return saveSchedule(); }
  if (d.act) return action(d.act, t);
});
function coerce(v) { return v === 'true' ? true : v === 'false' ? false : (v !== '' && !isNaN(v) ? +v : v); }
function zoneCtx(t) { const dv = device(t.closest('[data-dev]').dataset.dev); const zi = +t.closest('[data-zone]').dataset.zone; return { dv, zi }; }

async function action(a, t) {
  if (a.startsWith('fan-')) return fanAction(a);
  const sc = editScene();
  switch (a) {
    case 'new-scene': { const s = await api('new_scene', 'Neue Szene'); await refresh(); UI.editId = s.id; UI.layerId = null; UI.tab = 'effect'; return go('studio'); }
    case 'add-layer': {
      const l = { id: Math.random().toString(16).slice(2, 12), target: { type: 'fans' }, effect: 'rotor', params: await api('effect_defaults', 'rotor'), span: 'zone', enabled: true };
      sc.layers.push(l); UI.layerId = l.id; UI.tab = 'effect'; return saveScene(true);
    }
    case 'del-layer': sc.layers = sc.layers.filter(l => l.id !== UI.layerId); UI.layerId = null; return saveScene(true);
    case 'dup-layer': { const l = JSON.parse(JSON.stringify(curLayer())); l.id = Math.random().toString(16).slice(2, 12); sc.layers.push(l); UI.layerId = l.id; return saveScene(true); }
    case 'reset-params': { const ly = curLayer(); ly.params = await api('effect_defaults', ly.effect); return saveScene(true); }
    case 'del-scene': if (!confirm(`Szene „${sc.name}“ wirklich löschen?`)) return; await api('delete_scene', sc.id); UI.editId = null; await refresh(); toast('Szene gelöscht'); return renderPage();
    case 'export': {
      const blob = new Blob([JSON.stringify({ prisma: 1, scene: sc }, null, 1)], { type: 'application/json' });
      const aEl = document.createElement('a'); aEl.href = URL.createObjectURL(blob); aEl.download = sc.name.replace(/[^\w\- äöüÄÖÜß]/g, '') + '.prisma.json'; aEl.click(); return toast('Szene exportiert');
    }
    case 'import': {
      const inp = document.createElement('input'); inp.type = 'file'; inp.accept = '.json';
      inp.onchange = async () => { try { const j = JSON.parse(await inp.files[0].text()); const s = await api('import_scene', j.scene || j); await refresh(); UI.editId = s.id; toast('Szene importiert'); go('studio'); } catch (e) { toast('Ungültige Datei', true); } };
      return inp.click();
    }
    case 'rescan': await api('openrgb_rescan'); await refresh(); toast('Geräte neu eingelesen'); return renderPage();
    case 'kill-conflicts': { const k = await api('kill_conflicts', conflictCache.map(c => c.name)); conflictCache = await api('conflicts'); toast(k.length ? 'Beendet: ' + k.join(', ') : 'Nichts beendet (evtl. Adminrechte nötig)', !k.length); return renderPage(); }
    case 'reconnect': await api('openrgb_reconnect'); toast('Verbinde mit OpenRGB …'); setTimeout(async () => { await refresh(); renderPage(); }, 1500); return;
    case 'find-orgb': { const p = await api('openrgb_find'); if (p) { S.settings.openrgb_path = p; toast('Gefunden: ' + p); } else toast('OpenRGB.exe nicht gefunden', true); return renderPage(); }
    case 'pick-orgb': { const p = await api('pick_openrgb'); if (p) { S.settings.openrgb_path = p; renderPage(); } return; }
    case 'launch-orgb': { const r = await api('openrgb_launch'); toast(r.msg, !r.ok); setTimeout(async () => { await refresh(); renderPage(); }, 5000); return; }
    case 'reset-color': await api('set_settings', { gamma: 1, saturation: 100, wb_r: 100, wb_g: 100, wb_b: 100 }); await refresh(); return renderPage();
    case 'reset-scenes': if (!confirm('Alle Standard-Szenen auf Werkszustand zurücksetzen? Eigene Szenen bleiben erhalten.')) return; await api('reset_scenes'); await refresh(); toast('Standard-Szenen wiederhergestellt'); return renderPage();
    case 'quit': return api('win', 'quit');
    case 'upd-check': S.update = await api('update_check'); return renderPage();
    case 'upd-install': S.update = await api('update_install'); toast('Update wird installiert …'); return renderPage();
    case 'sched-add': S.schedule.push({ time: '22:00', action: 'scene', scene: 'night', days: [0, 1, 2, 3, 4, 5, 6], enabled: true }); return saveSchedule();
    case 'sched-templates':
      S.schedule.push({ time: '08:00', action: 'scene', scene: 'daylight', days: [0, 1, 2, 3, 4], enabled: true },
        { time: '21:30', action: 'scene', scene: 'candles', days: [0, 1, 2, 3, 4, 5, 6], enabled: true },
        { time: '23:30', action: 'scene', scene: 'night', days: [0, 1, 2, 3, 4, 5, 6], enabled: true },
        { time: '01:00', action: 'off', days: [0, 1, 2, 3, 4, 5, 6], enabled: true });
      toast('4 Vorlagen hinzugefügt'); return saveSchedule();
  }
}

document.addEventListener('input', e => {
  const t = e.target;
  if (t.type === 'range') { t.style.setProperty('--p', ((t.value - t.min) / (t.max - t.min) * 100) + '%'); }
  const d = t.dataset;
  if (t.id === 'masterR') { $('#mval').textContent = t.value + '%'; S.settings.master = +t.value; throttle('master', () => api('set_settings', { master: +t.value })); return; }
  if (t.id === 'q') { UI.q = t.value; const pos = t.selectionStart; renderPage(); const n = $('#q'); n.focus(); n.setSelectionRange(pos, pos); return; }
  if (d.p) { const ef = effect(curLayer().effect); const p = ef.params.find(x => x.key === d.p); t.nextElementSibling.textContent = fmt(t.value, p); return setParam(d.p, +t.value); }
  if (t.id === 'scName' || t.id === 'scDesc' || t.id === 'scCat') { const sc = editScene(); sc[{ scName: 'name', scDesc: 'desc', scCat: 'category' }[t.id]] = t.value; saveScene(); throttle('quick', renderQuick, 300); return; }
  if (d.setr) { const step = +t.step; t.nextElementSibling.textContent = fmtS(t.value, step, d.unit || ''); S.settings[d.setr] = +t.value; throttle('set' + d.setr, () => api('set_settings', { [d.setr]: +t.value })); return; }
  if (d.devBri !== undefined) { const dv = device(t.closest('[data-dev]').dataset.dev); t.nextElementSibling.textContent = t.value + '%'; dv.cfg.brightness = +t.value; throttle('db' + dv.key, () => api('set_device', dv.key, { brightness: +t.value })); return; }
  if (d.zoff !== undefined) { const { dv, zi } = zoneCtx(t); t.nextElementSibling.textContent = t.value + '%'; dv.cfg.zones[zi].offset = +t.value; throttle('zo' + dv.key + zi, () => api('set_zone', dv.key, zi, { offset: +t.value })); return; }
  if (d.sval !== undefined) { const i = +t.closest('[data-si]').dataset.si; S.schedule[i].value = +t.value; t.nextElementSibling.textContent = t.value + '%'; throttle('sv', () => saveSchedule(false), 300); return; }
});
document.addEventListener('change', async e => {
  const t = e.target, d = t.dataset;
  if (t.id === 'scenePick') { UI.editId = t.value; UI.layerId = null; return renderPage(); }
  if (d.lyen) { const ly = editScene().layers.find(l => l.id === d.lyen); ly.enabled = t.checked; return saveScene(true); }
  if (d.bool) return setParam(d.bool, t.checked, true);
  if (d.tgt) { const ly = curLayer(); ly.target[d.tgt] = d.tgt === 'zone' ? +t.value : t.value; if (d.tgt === 'key' && ly.target.type === 'zone') ly.target.zone = 0; return saveScene(true); }
  const haRefresh = () => setTimeout(async () => { await refresh(); if (UI.page === 'settings') renderPage(); }, 2500);
  if (d.set) { S.settings[d.set] = t.checked; await api('set_settings', { [d.set]: t.checked }); if (d.set.startsWith('mqtt_')) haRefresh(); return; }
  if (d.sett) {
    if (d.secret !== undefined && t.value === '••••••••') return;
    const v = t.type === 'number' ? (t.value === '' ? null : +t.value) : t.value; if (d.secret === undefined) S.settings[d.sett] = v;
    await api('set_settings', { [d.sett]: v }); if (d.sett.startsWith('mqtt_')) haRefresh(); return;
  }
  if (t.id === 'orgbPath') { S.settings.openrgb_path = t.value.trim(); await api('set_settings', { openrgb_path: t.value.trim() }); return renderPage(); }
  if (d.actAutostart !== undefined) { S.autostart = await api('set_autostart', t.checked); toast(S.autostart ? 'Startet jetzt mit Windows' : 'Autostart entfernt'); return; }
  if (d.devEn !== undefined) { const dv = device(t.closest('[data-dev]').dataset.dev); dv.cfg.enabled = t.checked; return api('set_device', dv.key, { enabled: t.checked }); }
  if (d.hwmode !== undefined) { const dv = device(t.closest('[data-dev]').dataset.dev); dv.cfg.control = 'hw:' + t.value; return api('set_device', dv.key, { control: dv.cfg.control }); }
  if (d.zen !== undefined) { const { dv, zi } = zoneCtx(t); dv.cfg.zones[zi].enabled = t.checked; return api('set_zone', dv.key, zi, { enabled: t.checked }); }
  if (d.zrev !== undefined) { const { dv, zi } = zoneCtx(t); dv.cfg.zones[zi].reverse = t.checked; return api('set_zone', dv.key, zi, { reverse: t.checked }); }
  if (d.zsizev !== undefined) { const { dv, zi } = zoneCtx(t); await api('resize_zone', dv.key, zi, +t.value); await refresh(); return renderPage(); }
  if (d.zfansv !== undefined) { const { dv, zi } = zoneCtx(t); const v = clamp(+t.value, 1, 12); dv.cfg.zones[zi].fans = v; await api('set_zone', dv.key, zi, { fans: v }); return renderPage(); }
  if (d.sen !== undefined) { S.schedule[+t.closest('[data-si]').dataset.si].enabled = t.checked; return saveSchedule(false); }
  if (d.stime !== undefined) { S.schedule[+t.closest('[data-si]').dataset.si].time = t.value; return saveSchedule(false); }
  if (d.sact !== undefined) { const e2 = S.schedule[+t.closest('[data-si]').dataset.si]; e2.action = t.value; if (t.value === 'scene' && !e2.scene) e2.scene = S.scenes[0].id; return saveSchedule(); }
  if (d.sfan !== undefined) { S.schedule[+t.closest('[data-si]').dataset.si].profile = t.value; return saveSchedule(false); }
  if (d.sscene !== undefined) { S.schedule[+t.closest('[data-si]').dataset.si].scene = t.value; return saveSchedule(false); }
});
document.addEventListener('toggle', e => { const t = e.target; if (t.dataset && t.dataset.palopen !== undefined) UI.palOpen = t.open ? t.dataset.palopen : null; }, true);
const thr = {};
function throttle(k, fn, ms = 60) { if (thr[k]) { thr[k].fn = fn; return; } thr[k] = { fn }; setTimeout(() => { const f = thr[k].fn; delete thr[k]; f(); }, ms); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePicker(); });

/* ------------------------------------------------------------ boot */
function initWin() {
  const ic = { min: 'min', max: 'max', close: 'close' };
  $$('[data-win]').forEach(b => b.innerHTML = icon(ic[b.dataset.win]));
  window.addEventListener('pywebviewready', () => document.body.classList.remove('web'));
  if (window.pywebview) document.body.classList.remove('web');
}
(async function boot() {
  initWin();
  try { await refresh(); } catch (e) { $('#main').innerHTML = `<div class="page"><div class="empty">${icon('warn')}<div>Verbindung zum Prisma-Dienst fehlgeschlagen.</div></div></div>`; return; }
  renderPage();
  poll();
  requestAnimationFrame(raf);
})();
