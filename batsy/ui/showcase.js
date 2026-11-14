// "Show what you can do" (batsy/showcase.py): a live CPU temperature dial with its last 90 seconds, every core,
// load / memory / network / battery / fans, beside capability tiles that light up as Batsy talks about them.
const NS = 'http://www.w3.org/2000/svg';
const C = 120, R = 92, START = -135, SWEEP = 270;  // the dial: a 270° arc, 30 °C bottom left round to 100 °C bottom right
const T_MIN = 30, T_MAX = 100, HISTORY = 90;
const ICONS = {
  wave: 'M4 10v4M8 7v10M12 3v18M16 7v10M20 10v4',
  print: 'M12 3a9 9 0 0 1 9 9v2M3 12a9 9 0 0 1 3.5-7.1M12 7a5 5 0 0 1 5 5v3M7 12a5 5 0 0 1 2-4M12 11v6M9 14v2a4 4 0 0 0 1.2 3',
  brain: 'M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3zM9 4v16M15 4v16',
  spark: 'M12 3l2.2 6.3L20.5 11.5l-6.3 2.2L12 20l-2.2-6.3L3.5 11.5l6.3-2.2zM19 3v4M17 5h4',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  files: 'M3 7V5h7l2 2h9v12H3zM3 7h18M8 13h8M8 16h5',
  home: 'M3 11l9-7 9 7M5 10v10h14V10M10 20v-6h4v6',
  gauge: 'M4 17a8 8 0 1 1 16 0M12 17l4.5-5.5M12 17h.01',
};

const $ = (root, s) => root.querySelector(s);
const svg = (tag, attrs) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};
const polar = (deg, r) => {
  const a = (deg - 90) * Math.PI / 180;  // 0° = straight up
  return [C + r * Math.cos(a), C + r * Math.sin(a)];
};
const arc = (from, to, r) => {
  const [x1, y1] = polar(from, r), [x2, y2] = polar(to, r);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)}A${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
};
const angle = t => START + SWEEP * Math.max(0, Math.min(1, (t - T_MIN) / (T_MAX - T_MIN)));
const heat = (t, high) => t >= high - 10 ? '#ff5c4d' : t >= 72 ? '#ff9a1f' : '#ffc20e';
const rate = b => b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB/s` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} KB/s` : `${Math.round(b)} B/s`;
const clock = s => {
  const d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24, m = Math.floor(s / 60) % 60;
  return d ? `${d}d ${h}h` : `${h}h ${String(m).padStart(2, '0')}m`;
};

export class Showcase {
  constructor(root) {
    this.root = root;
    this.history = [];
    this.shown = null;  // the number on the dial, eased towards the reading
    this.target = null;
    this.high = 95;
    this.drawDial();
  }

  drawDial() {
    const ticks = $(this.root, '.ticks');
    for (let t = T_MIN; t <= T_MAX; t += 5) {
      const major = t % 10 === 0, a = angle(t);
      const [x1, y1] = polar(a, R + 13), [x2, y2] = polar(a, R + (major ? 22 : 18));
      ticks.appendChild(svg('line', {x1, y1, x2, y2, class: major ? 'major' : ''}));
      if (major) {
        const [tx, ty] = polar(a, R + 33);
        const label = svg('text', {x: tx, y: ty});
        label.textContent = t;
        ticks.appendChild(label);
      }
    }
    $(this.root, '.track').setAttribute('d', arc(START, START + SWEEP, R));
    const value = $(this.root, '.value');
    value.setAttribute('d', arc(START, START + SWEEP, R));
    this.arcLen = R * SWEEP * Math.PI / 180;  // computed: the section is hidden, so nothing to measure
    value.style.strokeDasharray = `${this.arcLen} ${this.arcLen}`;
    value.style.strokeDashoffset = this.arcLen;
  }

  start(m) {
    this.high = +m.high || 95;
    this.history = [];
    this.shown = this.target = null;
    $(this.root, '.limit').setAttribute('d', arc(angle(this.high), START + SWEEP, R));
    const tiles = $(this.root, '.tiles');
    tiles.replaceChildren();
    (m.tiles || []).forEach((t, i) => {
      const li = document.createElement('li');
      li.className = 'tile';
      li.style.setProperty('--i', i);
      const icon = svg('svg', {viewBox: '0 0 24 24', 'aria-hidden': 'true'});
      icon.appendChild(svg('path', {d: ICONS[t.icon] || ICONS.spark}));
      const h = document.createElement('h3'), p = document.createElement('p');
      h.textContent = t.title;
      p.textContent = t.fact;
      li.append(icon, h, p);
      tiles.appendChild(li);
    });
    this.root.classList.remove('all');
    this.cores([], []);
  }

  focus(i) {
    const tiles = [...this.root.querySelectorAll('.tile')];
    tiles.forEach((t, j) => t.classList.toggle('on', j === i));
    this.root.classList.toggle('all', i < 0);  // the tour is over: everything lit
  }

  seen(i) { this.root.querySelectorAll('.tile')[i]?.classList.add('seen'); }

  stats(m) {
    if (m.temp != null) {
      this.target = +m.temp;
      if (this.shown == null) this.shown = this.target;
      this.history.push(this.target);
      if (this.history.length > HISTORY) this.history.shift();
      this.root.style.setProperty('--heat', heat(this.target, this.high));
      const value = $(this.root, '.value');
      const frac = (angle(this.target) - START) / SWEEP;
      value.style.strokeDashoffset = this.arcLen * (1 - frac);
      const cores = m.cores || [];
      $(this.root, '.hot').textContent = cores.length ? `Hottest core ${Math.round(Math.max(...cores))}°` : '';
      this.trace();
    } else {
      $(this.root, '.what').textContent = 'No temperature sensor';
    }
    this.cores(m.cores || [], m.load || []);
    const chips = [['CPU', `${Math.round((m.load || []).reduce((a, b) => a + b, 0) / Math.max(1, (m.load || []).length))}%`],
                   ['Memory', `${Math.round(m.ram)}%`], ['Down', rate(m.down || 0)], ['Up', rate(m.up || 0)]];
    if (m.battery != null) chips.push(['Battery', `${m.battery}%${m.plugged ? ' ⚡' : ''}`]);
    if ((m.fans || []).length) chips.push(['Fan', `${Math.max(...m.fans)} rpm`]);
    const dl = $(this.root, '.chips');
    dl.replaceChildren(...chips.slice(0, 6).map(([k, v]) => {
      const d = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = k; dd.textContent = v; d.append(dt, dd);
      return d;
    }));
    if (m.uptime != null) $(this.root, '.up').textContent = `live · up ${clock(m.uptime)}`;
  }

  cores(temps, load) {
    const box = $(this.root, '.cores');
    const n = temps.length || load.length;
    if (temps.length && load.length > n) {  // sensors per physical core, load per thread: average each core's threads
      load = temps.map((_, i) => {
        const mine = load.filter((_, j) => j % n === i);
        return mine.reduce((x, y) => x + y, 0) / mine.length;
      });
    }
    while (box.children.length > n) box.lastChild.remove();
    while (box.children.length < n) {
      const c = document.createElement('div');
      c.className = 'core';
      c.innerHTML = '<b></b><div class="bar"><i></i></div><span></span>';
      box.appendChild(c);
    }
    [...box.children].forEach((c, i) => {
      const t = temps[i], l = load[i];
      const fill = t != null ? (t - T_MIN) / (T_MAX - T_MIN) : (l || 0) / 100;
      c.querySelector('b').textContent = t != null ? `${Math.round(t)}°` : `${Math.round(l || 0)}%`;
      const bar = c.querySelector('i');
      bar.style.height = `${Math.max(3, Math.min(100, fill * 100))}%`;
      bar.style.setProperty('--c', t != null ? heat(t, this.high) : '#ffc20e');
      c.querySelector('span').textContent = `C${i}${t != null && l != null ? ` · ${Math.round(l)}%` : ''}`;
    });
  }

  trace() {
    const h = this.history;
    if (h.length < 2) return;
    // Fit the readings; the warning line joins in only once they get within 12° of it.
    const near = Math.max(...h) >= this.high - 12;
    const lo = Math.min(...h) - 4, hi = Math.max(Math.max(...h) + 4, near ? this.high + 2 : -Infinity, lo + 12);
    const y = t => 64 - 64 * (t - lo) / (hi - lo);
    const step = 300 / (HISTORY - 1), x0 = 300 - step * (h.length - 1);
    const pts = h.map((t, i) => `${(x0 + i * step).toFixed(1)} ${y(t).toFixed(1)}`);
    $(this.root, '.line').setAttribute('d', 'M' + pts.join('L'));
    $(this.root, '.area').setAttribute('d', `M${x0.toFixed(1)} 64L${pts.join('L')}L300 64Z`);
    const lim = $(this.root, '.lim');
    lim.style.display = near ? '' : 'none';
    lim.setAttribute('y1', y(this.high)); lim.setAttribute('y2', y(this.high));
    $(this.root, '.range').textContent = `${Math.round(Math.min(...h))}° – ${Math.round(Math.max(...h))}°`;
  }

  frame(dt) {  // ease the big number towards the latest reading
    if (this.target == null) return;
    this.shown += (this.target - this.shown) * Math.min(1, dt * 4);
    $(this.root, '.t').textContent = Math.round(this.shown);
  }
}
