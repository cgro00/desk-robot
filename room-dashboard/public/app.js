// Room dashboard page. Asks the local server (server.py) for the room's
// readings, the weather, and the history. Opened as a plain file, without
// the server, it shows synthetic demo data so you can see the design.
'use strict';

const $ = (id) => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';

// What counts as good, worth a look, or time to act. Adjust to taste.
const LIMITS = {
  temperature: [
    [18, 'bad', 'Kallt'], [19.5, 'warn', 'Lite svalt'], [23.5, 'good', 'Behagligt'],
    [25.5, 'warn', 'Varmt'], [Infinity, 'bad', 'För varmt'],
  ],
  humidity: [
    [25, 'bad', 'Mycket torr luft'], [35, 'warn', 'Torr luft'], [60, 'good', 'Bra fuktighet'],
    [70, 'warn', 'Fuktigt'], [Infinity, 'bad', 'Mycket fuktigt'],
  ],
  co2: [
    [800, 'good', 'Frisk luft'], [1000, 'good', 'Okej luft'], [1400, 'warn', 'Dags att vädra snart'],
    [Infinity, 'bad', 'Vädra nu'],
  ],
};

const CHARTS = {
  temperature: {
    title: 'Temperatur inne och ute', unit: '°C', decimals: 1,
    series: [{ key: 'temperature', label: 'Inne', color: '#d79971' }, { key: 'outdoor', label: 'Ute', color: '#8aaeb0' }],
    band: [19.5, 23.5, 'Behagligt'],
    note: 'Utetemperaturen kommer från väderprognosen för din ort, inte från en egen givare.',
  },
  humidity: {
    title: 'Luftfuktighet', unit: '%', decimals: 0,
    series: [{ key: 'humidity', label: 'Inne', color: '#8aaeb0' }],
    band: [35, 60, 'Bra fuktighet'],
    note: '35–60 % är behagligt för de flesta. Vintertid blir inomhusluften ofta torr.',
  },
  co2: {
    title: 'Koldioxid', unit: 'ppm', decimals: 0,
    series: [{ key: 'co2', label: 'Inne', color: '#d3b877' }],
    line: [1000, 'Vädra över 1000'],
    note: 'Utomhusluft ligger runt 420 ppm. Stiger det över 1000 blir luften tung och det är svårare att koncentrera sig.',
  },
};

const state = { now: null, history: [], chart: 'temperature', hours: 24, offline: false };

// ── small helpers ───────────────────────────────────────────────────────────
function judge(metric, v) {
  if (v == null) return ['none', 'Ingen mätning'];
  for (const [max, level, text] of LIMITS[metric]) if (v < max) return [level, text];
  return ['none', ''];
}
const fmt = (v, d = 1) => (v == null || !Number.isFinite(v) ? '–' : (Math.abs(v) < 1e-9 ? 0 : v).toLocaleString('sv-SE', { minimumFractionDigits: d, maximumFractionDigits: d }));
const hm = (t) => new Date(t * 1000).toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}
function setText(id, text) { $(id).textContent = text; }
// SVG elements ignore the HTML `hidden` attribute, so toggle `display`.
function show(id, on) { $(id).setAttribute('display', on ? 'inline' : 'none'); }

// ── weather symbols ─────────────────────────────────────────────────────────
function weatherKind(code) {
  if (code == null) return ['unknown', 'Okänt'];
  if (code === 0) return ['clear', 'Klart'];
  if (code <= 2) return ['partly', code === 1 ? 'Mestadels klart' : 'Halvklart'];
  if (code === 3) return ['cloudy', 'Mulet'];
  if (code === 45 || code === 48) return ['fog', 'Dimma'];
  if (code >= 51 && code <= 57) return ['rain', 'Duggregn'];
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return ['rain', code >= 80 ? 'Regnskurar' : 'Regn'];
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return ['snow', 'Snö'];
  if (code >= 95) return ['thunder', 'Åska'];
  return ['cloudy', 'Mulet'];
}
// Fixed markup (no data from outside goes in), so innerHTML is safe here.
function icon(kind, day = true) {
  const sun = day
    ? '<circle cx="22" cy="20" r="8" fill="#e9c879"/><g stroke="#e9c879" stroke-width="2" stroke-linecap="round"><path d="M22 5v4M22 31v4M7 20h4M33 20h4M11.5 9.5l2.8 2.8M29.7 27.7l2.8 2.8M11.5 30.5l2.8-2.8M29.7 12.3l2.8-2.8"/></g>'
    : '<path d="M27 8a12 12 0 1 0 9 17A10 10 0 0 1 27 8z" fill="#e3dec2"/>';
  const cloud = (x = 0, y = 0, c = '#cfd7cc') => `<path transform="translate(${x} ${y})" d="M14 38h22a8 8 0 0 0 0-16 11 11 0 0 0-21-2 8 8 0 0 0-1 18z" fill="${c}"/>`;
  const parts = {
    clear: sun,
    partly: sun + cloud(4, 4),
    cloudy: cloud(-2, -2, '#9aa596') + cloud(4, 4),
    fog: '<g stroke="#b9c1b4" stroke-width="3" stroke-linecap="round"><path d="M8 18h32M5 26h32M10 34h30"/></g>',
    rain: cloud(0, -6) + '<g stroke="#8fb5c4" stroke-width="2.5" stroke-linecap="round"><path d="M17 38l-3 6M25 38l-3 6M33 38l-3 6"/></g>',
    snow: cloud(0, -6) + '<g fill="#eef2ee"><circle cx="16" cy="41" r="2"/><circle cx="24" cy="44" r="2"/><circle cx="32" cy="41" r="2"/></g>',
    thunder: cloud(0, -6, '#8f9a8b') + '<path d="M25 32l-6 9h5l-3 7 9-11h-5l3-5z" fill="#e9c879"/>',
    unknown: cloud(0, 0, '#6f7a6b'),
  };
  return `<svg viewBox="0 0 48 48" aria-hidden="true">${parts[kind] || parts.unknown}</svg>`;
}

// ── demo data (only when the page is opened without the server) ────────────
function demoValues(t) {
  const d = new Date(t * 1000), hour = d.getHours() + d.getMinutes() / 60;
  const home = hour >= 16 || hour < 8;
  let co2 = 520 + (home ? 650 * Math.min(1, ((hour - 16 + 24) % 24) / 5) : 80) + 40 * Math.sin(t / 1300);
  if (hour >= 21 && hour < 21.5) co2 = 480;
  return {
    temperature: +(21 + 1.2 * Math.sin((hour - 9) / 24 * 2 * Math.PI) + 0.15 * Math.sin(t / 900)).toFixed(1),
    humidity: +(38 + 6 * Math.sin((hour - 3) / 24 * 2 * Math.PI) + 2 * Math.sin(t / 2000)).toFixed(1),
    co2: Math.round(co2),
    outdoor: +(4 + 3.5 * Math.sin((hour - 9) / 24 * 2 * Math.PI) + 0.6 * Math.sin(t / 5000)).toFixed(1),
  };
}
function demoNow() {
  const t = Date.now() / 1000, today = new Date(); today.setHours(0, 0, 0, 0);
  const midnight = today.getTime() / 1000;
  const hours = [];
  for (let i = 0; i < 13; i++) {
    const at = Math.floor(t / 3600) * 3600 + i * 3600;
    hours.push({ at, temperature: demoValues(at).outdoor, rain_chance: [5, 10, 20, 40, 60, 45, 20][i % 7], code: [2, 3, 3, 61, 61, 3, 2][i % 7] });
  }
  return {
    title: 'Mitt rum', demo: true, source: 'Demodata (påhittade värden)', location: 'Exempelstad', poll_seconds: 60,
    room: { at: t, values: demoValues(t), errors: {} },
    weather: {
      fetched_at: t,
      current: { temperature: demoValues(t).outdoor, feels_like: demoValues(t).outdoor - 3, humidity: 82, wind: 4.2, precipitation: 0, code: 3, is_day: new Date().getHours() >= 7 && new Date().getHours() < 16 },
      today: { min: 1.2, max: 7.4, sunrise: midnight + 7.4 * 3600, sunset: midnight + 16.0 * 3600 },
      hours,
    },
    weather_error: '',
  };
}
function demoHistory(hours) {
  const out = [], end = Date.now() / 1000;
  for (let t = end - hours * 3600; t <= end; t += hours > 24 ? 1800 : 300) out.push({ at: t, values: demoValues(t) });
  return out;
}

// ── data ────────────────────────────────────────────────────────────────────
async function getJSON(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
async function refreshNow() {
  try {
    state.now = await getJSON('./api/now');
    state.offline = false;
    // Demo without a location: show made-up weather too, so the whole page is visible.
    if (state.now.demo && !state.now.weather) {
      const d = demoNow();
      state.now.weather = d.weather;
      state.now.location = state.now.location || d.location;
    }
  } catch (e) {
    if (location.protocol === 'file:' || !state.now || state.now.demo) {
      state.now = demoNow();
      state.offline = location.protocol === 'file:' ? 'file' : 'down';
    } else {
      state.offline = 'down';
    }
  }
  renderNow();
}
async function refreshHistory() {
  try {
    if (state.offline === 'file') throw new Error('no server');
    state.history = (await getJSON(`./api/history?hours=${state.hours}`)).samples;
  } catch (e) {
    if (!state.now || state.now.demo) state.history = demoHistory(state.hours);
  }
  renderChart();
}

// ── rendering: header, cards, scene ────────────────────────────────────────
function tick() {
  const d = new Date();
  setText('clock', d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' }));
  setText('date', d.toLocaleDateString('sv-SE', { weekday: 'long', day: 'numeric', month: 'long' }));
}

function renderNow() {
  const n = state.now;
  if (!n) return;
  document.title = n.title;
  setText('title', n.title);
  setText('source', n.source.toUpperCase());
  const mode = $('mode');
  mode.textContent = n.demo ? 'DEMO · PÅHITTADE VÄRDEN' : 'LIVE';
  mode.classList.toggle('live', !n.demo);

  const msg = $('message');
  const problems = [];
  if (state.offline === 'file') problems.push('Sidan är öppnad som fil, så den visar demodata. Starta server.py för att se ditt rum.');
  else if (state.offline === 'down') problems.push('Servern svarar inte. Visar senast kända värden.');
  for (const [k, why] of Object.entries((n.room && n.room.errors) || {})) problems.push(`${k === '*' ? 'Sensorer' : k}: ${why}`);
  msg.hidden = !problems.length;
  msg.textContent = problems.join(' · ');

  const v = (n.room && n.room.values) || {};
  for (const metric of ['temperature', 'humidity', 'co2']) {
    const card = $(`card-${metric}`);
    const configured = metric in v;
    const [level, text] = configured ? judge(metric, v[metric]) : ['none', 'Ingen sensor kopplad'];
    card.dataset.level = level;
    card.querySelector('.num').textContent = fmt(v[metric], metric === 'co2' ? 0 : 1);
    card.querySelector('.status').textContent = text;
    card.querySelector('.sub').textContent = trendText(metric);
  }
  renderAdvice(v, n.weather);
  renderScene(v, n.weather);
  renderWeather(n);
  setText('updated', n.room && n.room.at ? `Senast uppdaterat ${hm(n.room.at)}` : '');
}

// Change over the last hour, from the history.
function trendText(metric) {
  const h = state.history.filter((s) => s.values[metric] != null);
  if (h.length < 2) return '';
  const last = h[h.length - 1], target = last.at - 3600;
  let then = null;
  for (const s of h) { if (s.at <= target) then = s; else break; }
  if (!then || last.at - then.at > 5400) return '';
  const diff = last.values[metric] - then.values[metric];
  const unit = { temperature: '°', humidity: ' %', co2: ' ppm' }[metric];
  const d = metric === 'co2' ? 0 : 1;
  const small = { temperature: 0.2, humidity: 1, co2: 30 }[metric];
  if (Math.abs(diff) < small) return 'Stabilt senaste timmen';
  return `${diff > 0 ? '↑ +' : '↓ −'}${fmt(Math.abs(diff), d)}${unit} senaste timmen`;
}

function renderAdvice(v, w) {
  const out = w && w.current ? w.current.temperature : (v.outdoor ?? null);
  const tips = [];
  let headline = 'Allt ser bra ut i rummet.';
  const [co2Level] = judge('co2', v.co2), [tLevel, tText] = judge('temperature', v.temperature), [hLevel, hText] = judge('humidity', v.humidity);
  if (co2Level === 'bad') headline = 'Vädra nu, luften är tung.';
  else if (co2Level === 'warn') headline = 'Dags att vädra snart.';
  else if (tLevel === 'bad') headline = `${tText} i rummet.`;
  else if (hLevel === 'bad') headline = `${hText}.`;
  else if (tLevel === 'warn' || hLevel === 'warn') headline = 'Nästan perfekt i rummet.';
  if (v.temperature == null && v.humidity == null && v.co2 == null) headline = 'Inga mätvärden just nu.';

  if (co2Level === 'bad' || co2Level === 'warn') {
    tips.push(out != null && out < 5
      ? 'Det är kallt ute: öppna helt i 5 minuter (korsdrag) istället för att ha fönstret på glänt länge.'
      : 'Öppna fönstret en stund, gärna med korsdrag.');
  }
  if (v.temperature != null && v.temperature > 24 && out != null && out < v.temperature - 3) tips.push(`Svalare ute (${fmt(out)}°). Ett öppet fönster kyler rummet.`);
  if (v.temperature != null && v.temperature < 19 && out != null && out < v.temperature) tips.push('Håll fönstret stängt och dra för gardinen när det blir mörkt.');
  if (hLevel === 'warn' || hLevel === 'bad') tips.push(v.humidity < 35 ? 'Torr luft: en skål vatten på elementet eller en luftfuktare hjälper.' : 'Fuktigt: vädra och undvik att torka tvätt i rummet.');
  if (!tips.length && w && w.hours) {
    const rain = w.hours.slice(1, 7).find((h) => (h.rain_chance || 0) >= 50);
    if (rain) tips.push(`Regn troligt runt ${hm(rain.at)}. Ta med paraply om du ska ut.`);
  }
  setText('headline', headline);
  setText('advice', tips.join(' ') || 'Bra temperatur, frisk luft och lagom fuktighet.');
}

function isDay(w) {
  const t = Date.now() / 1000;
  if (w && w.today && w.today.sunrise && w.today.sunset) return t >= w.today.sunrise && t < w.today.sunset;
  if (w && w.current) return !!w.current.is_day;
  const h = new Date().getHours();
  return h >= 7 && h < 18;
}
function renderScene(v, w) {
  const day = isDay(w);
  const t = Date.now() / 1000;
  const nearEdge = w && w.today && w.today.sunset && (Math.abs(t - w.today.sunset) < 2700 || Math.abs(t - w.today.sunrise) < 2700);
  const [top, bottom] = nearEdge ? ['#6a6a8c', '#e0a879'] : day ? ['#5d8db0', '#b9cfc4'] : ['#121b2b', '#2a3a4e'];
  $('sky-top').setAttribute('stop-color', top);
  $('sky-bottom').setAttribute('stop-color', bottom);
  $('moon-bite').setAttribute('fill', top);
  const kind = w && w.current ? weatherKind(w.current.code)[0] : 'clear';
  show('sun', !(!day || kind === 'cloudy' || kind === 'rain' || kind === 'snow' || kind === 'thunder' || kind === 'fog'));
  show('moon', !(day || kind !== 'clear' && kind !== 'partly'));
  show('stars', !(day || kind !== 'clear'));
  show('clouds', !(kind === 'clear'));
  show('rain', !(kind !== 'rain' && kind !== 'thunder'));
  show('snow', !(kind !== 'snow'));
  show('lamp-light', !(day));
  $('wall-temp').textContent = v.temperature == null ? '–' : `${fmt(v.temperature)}°`;
  $('wall-co2').textContent = v.co2 == null ? (v.humidity == null ? '' : `${fmt(v.humidity, 0)} %`) : `${fmt(v.co2, 0)} ppm`;
  const [co2Level] = judge('co2', v.co2);
  $('air-dot').setAttribute('fill', { good: '#aebf91', warn: '#d3b877', bad: '#e07f62' }[co2Level] || '#9fac93');
  // The room gets a little hazy as the air goes stale.
  const haze = v.co2 == null ? 0 : Math.max(0, Math.min(0.16, (v.co2 - 900) / 1000 * 0.16));
  $('haze').setAttribute('opacity', haze.toFixed(3));
}

function renderWeather(n) {
  const w = n.weather;
  setText('place', n.location ? `Väder i ${n.location}` : 'Väder');
  if (!w || !w.current) {
    setText('out-temp', '–'); setText('out-desc', 'Inget väder'); setText('out-more', '');
    setText('weather-note', n.weather_error || 'Hämtar väder…');
    $('weather-icon').innerHTML = icon('unknown');
    $('hours').replaceChildren();
    return;
  }
  const c = w.current, [kind, desc] = weatherKind(c.code), day = isDay(w);
  $('weather-icon').innerHTML = icon(kind, day);
  setText('out-temp', fmt(c.temperature));
  setText('out-desc', desc);
  const more = [];
  if (c.feels_like != null) more.push(`Känns som ${fmt(c.feels_like)}°`);
  if (c.wind != null) more.push(`Vind ${fmt(c.wind)} m/s`);
  if (c.humidity != null) more.push(`Fukt ${fmt(c.humidity, 0)} %`);
  setText('out-more', more.join(' · '));
  setText('out-minmax', w.today && w.today.min != null ? `${fmt(w.today.min)}° / ${fmt(w.today.max)}°` : '–');
  setText('sunrise', w.today && w.today.sunrise ? hm(w.today.sunrise) : '–');
  setText('sunset', w.today && w.today.sunset ? hm(w.today.sunset) : '–');
  const hours = $('hours');
  hours.replaceChildren();
  for (const h of (w.hours || []).filter((x) => x.at > Date.now() / 1000 - 1800).slice(0, 12)) {
    const div = document.createElement('div');
    div.className = 'hour';
    const time = document.createElement('span');
    time.textContent = new Date(h.at * 1000).getHours().toString().padStart(2, '0');
    const sunUp = w.today && w.today.sunrise ? (h.at >= w.today.sunrise && h.at < w.today.sunset) || h.at > w.today.sunrise + 86400 : true;
    const ic = document.createElement('div');
    ic.innerHTML = icon(weatherKind(h.code)[0], sunUp);
    const temp = document.createElement('b');
    temp.textContent = `${fmt(h.temperature, 0)}°`;
    const rain = document.createElement('span');
    rain.className = 'rain';
    rain.textContent = h.rain_chance ? `${h.rain_chance}%` : '';
    div.append(time, ic, temp, rain);
    hours.appendChild(div);
  }
  setText('weather-note', n.demo ? 'Påhittat demoväder.' : `Prognos från Open-Meteo, hämtad ${hm(w.fetched_at)}.`);
}

// ── rendering: chart ───────────────────────────────────────────────────────
function renderChart() {
  const spec = CHARTS[state.chart];
  setText('chart-title', spec.title);
  setText('chart-note', spec.note);
  const legend = $('legend');
  legend.replaceChildren();
  for (const s of spec.series) {
    const span = document.createElement('span');
    span.style.setProperty('--c', s.color);
    span.textContent = s.label;
    legend.appendChild(span);
  }
  if (spec.band) {
    const span = document.createElement('span');
    span.className = 'band';
    span.style.setProperty('--c', '#aebf91');
    span.textContent = spec.band[2];
    legend.appendChild(span);
  }

  const box = $('chart');
  box.replaceChildren();
  const W = Math.max(300, box.clientWidth), H = box.clientHeight || 290;
  const pad = { l: 44, r: 12, t: 10, b: 26 };
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': spec.title }, box);
  const end = Date.now() / 1000, start = end - state.hours * 3600;
  const samples = state.history.filter((s) => s.at >= start);
  const vals = [];
  for (const s of samples) for (const ser of spec.series) if (s.values[ser.key] != null) vals.push(s.values[ser.key]);
  if (!vals.length) {
    el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'empty' }, svg).textContent = 'Inga mätningar ännu för den här perioden.';
    return;
  }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (spec.band) { lo = Math.min(lo, spec.band[0]); hi = Math.max(hi, spec.band[1]); }
  if (spec.line) hi = Math.max(hi, spec.line[0] * 1.05);
  const span = Math.max(hi - lo, state.chart === 'co2' ? 200 : 2);
  lo -= span * 0.08; hi = lo + span * 1.16;
  const x = (t) => pad.l + (t - start) / (end - start) * (W - pad.l - pad.r);
  const y = (v) => H - pad.b - (v - lo) / (hi - lo) * (H - pad.t - pad.b);

  // grid + y labels
  const step = niceStep((hi - lo) / 4);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    el('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: 'grid' }, svg);
    el('text', { x: pad.l - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }, svg).textContent = fmt(v, step < 1 ? 1 : 0);
  }
  // x labels
  const xStep = state.hours <= 6 ? 3600 : state.hours <= 24 ? 4 * 3600 : 86400;
  for (let t = Math.ceil(start / xStep) * xStep; t <= end; t += xStep) {
    const label = state.hours > 24
      ? new Date(t * 1000).toLocaleDateString('sv-SE', { weekday: 'short' })
      : hm(t);
    el('text', { x: x(t), y: H - 6, 'text-anchor': 'middle', class: 'axis' }, svg).textContent = label;
  }
  if (spec.band) {
    el('rect', { x: pad.l, width: W - pad.l - pad.r, y: y(spec.band[1]), height: y(spec.band[0]) - y(spec.band[1]), fill: '#aebf91', opacity: 0.07 }, svg);
  }
  if (spec.line) {
    el('line', { x1: pad.l, x2: W - pad.r, y1: y(spec.line[0]), y2: y(spec.line[0]), stroke: '#e07f62', 'stroke-dasharray': '5 5', opacity: 0.7 }, svg);
    el('text', { x: W - pad.r, y: y(spec.line[0]) - 6, 'text-anchor': 'end', class: 'axis', fill: '#e07f62' }, svg).textContent = spec.line[1];
  }
  // lines, broken where readings are missing or far apart
  const gap = Math.max(600, (state.now ? state.now.poll_seconds : 60) * 3, state.hours > 24 ? 3600 : 0);
  for (const ser of spec.series) {
    let d = '', prev = null;
    for (const s of samples) {
      const v = s.values[ser.key];
      if (v == null) { prev = null; continue; }
      d += `${prev && s.at - prev <= gap ? 'L' : 'M'}${x(s.at).toFixed(1)} ${y(v).toFixed(1)}`;
      prev = s.at;
    }
    el('path', { d, fill: 'none', stroke: ser.color, 'stroke-width': 2.2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
  }
  // hover readout
  const cursor = el('line', { y1: pad.t, y2: H - pad.b, stroke: '#9fac93', 'stroke-width': 1, opacity: 0 }, svg);
  const tip = document.createElement('div');
  tip.className = 'tip';
  tip.hidden = true;
  box.appendChild(tip);
  const hit = el('rect', { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: 'transparent' }, svg);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * (W / r.width);
    const t = start + (px - pad.l) / (W - pad.l - pad.r) * (end - start);
    let best = null;
    for (const s of samples) if (!best || Math.abs(s.at - t) < Math.abs(best.at - t)) best = s;
    if (!best) return;
    cursor.setAttribute('x1', x(best.at)); cursor.setAttribute('x2', x(best.at)); cursor.setAttribute('opacity', 0.6);
    tip.hidden = false;
    tip.replaceChildren();
    tip.append(`${state.hours > 24 ? new Date(best.at * 1000).toLocaleDateString('sv-SE', { weekday: 'short' }) + ' ' : ''}${hm(best.at)}  `);
    for (const ser of spec.series) {
      const b = document.createElement('b');
      b.style.color = ser.color;
      b.textContent = ` ${ser.label} ${fmt(best.values[ser.key], spec.decimals)} ${spec.unit}`;
      tip.appendChild(b);
    }
    tip.style.left = `${(x(best.at) / W) * 100}%`;
    tip.style.top = `${pad.t + 10}px`;
  };
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerleave', () => { tip.hidden = true; cursor.setAttribute('opacity', 0); });
}
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * p) return m * p;
  return 10 * p;
}

// ── wiring ─────────────────────────────────────────────────────────────────
for (const b of document.querySelectorAll('.tabs button')) {
  b.addEventListener('click', () => {
    state.chart = b.dataset.chart;
    for (const o of document.querySelectorAll('.tabs button')) o.setAttribute('aria-selected', o === b ? 'true' : 'false');
    renderChart();
  });
}
$('range').addEventListener('change', (e) => { state.hours = +e.target.value; refreshHistory(); });
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 150); });

tick();
setInterval(tick, 1000);
(async () => {
  await refreshNow();
  await refreshHistory();
  renderNow(); // trends need the history
  setInterval(async () => { await refreshNow(); }, 30000);
  setInterval(async () => { await refreshHistory(); renderNow(); }, 60000);
})();
