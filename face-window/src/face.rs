// Port of FaceKit in server/brain/dashboard.html (itself a port of firmware/src/face.cpp): same pack format, same
// animation, same 128x64 one-bit picture.

use serde_json::Value;
use std::collections::{HashMap, HashSet};

pub const W: usize = 128;
pub const H: usize = 64;
const WF: f32 = W as f32;
const HF: f32 = H as f32;

pub const EMOTIONS: [&str; 12] = ["neutral", "happy", "sad", "angry", "surprised", "sleepy", "thinking",
                                  "curious", "skeptical", "excited", "love", "confused"];
const EYE_KEYS: [&str; 10] = ["w", "h", "rt", "rb", "st", "sb", "lid", "cheek", "dx", "dy"];
const EYE_DEF: Eye = [34., 34., 12., 12., 0., 0., 0., 0., 0., 0.];
const LIMITS: [(f32, f32); 10] = [(4., 120.), (2., 64.), (0., 32.), (0., 32.), (-30., 30.), (-30., 30.),
                                  (0., 40.), (0., 40.), (-30., 30.), (-30., 30.)];
const EW: usize = 0;
const EH: usize = 1;
const RT: usize = 2;
const RB: usize = 3;
const ST: usize = 4;
const SB: usize = 5;
const LID: usize = 6;
const CHEEK: usize = 7;
const DX: usize = 8;
const DY: usize = 9;

const BANG: [&str; 10] = ["###", "###", "###", "###", "###", "###", "###", "...", "###", "###"];
const QUESTION: [&str; 11] = [".#####.", "##...##", "##...##", ".....##", "....##.", "...##..", "..##...", "..##...",
                              ".......", "..##...", "..##..."];
const HEART: [&str; 6] = [".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."];

type Eye = [f32; 10];

#[derive(Clone)]
pub struct Look {
    l: Eye,
    r: Eye,
    gaze_x: f32,
    gaze_y: f32,
    pop: f32,
    smile: f32,
    fx: HashSet<String>,
}

#[derive(Clone)]
pub struct Pack {
    gap: f32,
    y: f32,
    cyclops: bool,
    outline: bool,
    glint: bool,
    mouth: u8,
    asleep: Eye,
    looks: HashMap<String, Look>,
}

fn clamp(x: f32, lo: f32, hi: f32) -> f32 {
    if x < lo { lo } else if x > hi { hi } else { x }
}

fn approach(c: f32, t: f32, f: f32) -> f32 {
    c + (t - c) * f
}

fn num(v: &Value, key: &str) -> Option<f32> {
    v.get(key).and_then(Value::as_f64).map(|x| x as f32)
}

fn read_eye(o: Option<&Value>, base: &Eye) -> Eye {
    let mut e = *base;
    if let Some(o) = o {
        for (i, k) in EYE_KEYS.iter().enumerate() {
            if let Some(x) = num(o, k) {
                e[i] = clamp(x, LIMITS[i].0, LIMITS[i].1);
            }
        }
    }
    e
}

/// A pack file -> what the drawing code uses (Face::applyPackJson). `fallback` fills emotions the pack leaves out.
pub fn resolve(p: &Value, fallback: Option<&Pack>) -> Pack {
    let asleep_base = fallback.map(|f| f.asleep).unwrap_or([30., 3., 1., 1., 0., 0., 0., 0., 0., 0.]);
    let mut looks = HashMap::new();
    for name in EMOTIONS {
        let look = match p.get("emotions").and_then(|e| e.get(name)) {
            Some(e) => {
                let l = read_eye(Some(e), &EYE_DEF);
                let gaze = |i: usize| e.get("gaze").and_then(|g| g.get(i)).and_then(Value::as_f64).unwrap_or(0.) as f32;
                Look {
                    l,
                    r: read_eye(e.get("right"), &l),
                    pop: clamp(num(e, "pop").unwrap_or(0.12), 0., 0.6),
                    smile: clamp(num(e, "smile").unwrap_or(0.), -6., 6.),
                    gaze_x: clamp(gaze(0), -20., 20.),
                    gaze_y: clamp(gaze(1), -20., 20.),
                    fx: e.get("fx").and_then(Value::as_array).into_iter().flatten()
                        .filter_map(|v| v.as_str().map(String::from)).collect(),
                }
            }
            None => fallback.and_then(|f| f.looks.get(name).cloned()).unwrap_or(Look {
                l: EYE_DEF, r: EYE_DEF, gaze_x: 0., gaze_y: 0., pop: 0.12, smile: 0., fx: HashSet::new(),
            }),
        };
        looks.insert(name.to_string(), look);
    }
    Pack {
        gap: clamp(num(p, "gap").unwrap_or(16.), 0., 60.),
        y: clamp(num(p, "y").unwrap_or(0.), -20., 20.),
        cyclops: p.get("cyclops").and_then(Value::as_bool).unwrap_or(false),
        outline: p.get("outline").and_then(Value::as_bool).unwrap_or(false),
        glint: p.get("glint").and_then(Value::as_bool).unwrap_or(true),
        mouth: match p.get("mouth").and_then(Value::as_str) { Some("always") => 1, Some("never") => 2, _ => 0 },
        asleep: read_eye(p.get("asleep"), &asleep_base),
        looks,
    }
}

struct Geo {
    cx: f32, w: f32, st: f32, sb: f32, lid: f32, cheek: f32, inner: f32,
    left: f32, right: f32, top: f32, bot: f32, rt: f32, rb: f32,
}

fn in_eye(g: &Geo, x: f32, y: f32) -> bool {
    if x < g.left || x >= g.right || y < g.top || y >= g.bot {
        return false;
    }
    let u = (x - g.cx) / (g.w * 0.5) * g.inner;
    let top_cut = if g.st >= 0. { g.st * (u + 1.) * 0.5 } else { -g.st * (1. - u) * 0.5 };
    let bot_cut = if g.sb >= 0. { g.sb * (u + 1.) * 0.5 } else { -g.sb * (1. - u) * 0.5 };
    let hh = g.bot - g.top;
    let yy = y - top_cut * (g.bot - y) / hh + bot_cut * (y - g.top) / hh;
    if yy < g.top + g.lid || yy >= g.bot {
        return false;
    }
    if yy < g.top + g.rt {
        let qx = clamp(x, g.left + g.rt, g.right - g.rt);
        let (dx, dy) = (x - qx, yy - (g.top + g.rt));
        if dx * dx + dy * dy > g.rt * g.rt {
            return false;
        }
    }
    if yy > g.bot - g.rb {
        let qx = clamp(x, g.left + g.rb, g.right - g.rb);
        let (dx, dy) = (x - qx, yy - (g.bot - g.rb));
        if dx * dx + dy * dy > g.rb * g.rb {
            return false;
        }
    }
    if g.cheek > 0.5 {
        let rr = g.w;
        let (dx, dy) = (x - g.cx, y - (g.bot + rr - g.cheek));
        if dx * dx + dy * dy < rr * rr {
            return false;
        }
    }
    true
}

#[derive(Clone, Copy, Default)]
struct Particle { alive: bool, x: f32, y: f32, age: f32 }

pub struct Sim {
    pack: Pack,
    pub buf: [u8; W * H],
    emotion: String,
    asleep: bool,
    pub talking: bool,
    pub mouth_level: f32,
    mouth: f32,
    blink: f32,
    blink_closing: bool,
    gaze_x: f32, gaze_y: f32, gtx: f32, gty: f32,
    pupil_x: f32, pupil_y: f32, ptx: f32, pty: f32,
    pop: f32, squint: f32, tear_y: f32, think_dots: i32, jitter: f32,
    hearts: [Particle; 3], heart_side: bool, zeds: [Particle; 3],
    breath: f32, twitch: f32, frame: u64,
    next_blink: f64, next_saccade: f64, next_squint: f64, next_pupil: f64, next_tear: f64, next_dot: f64,
    next_heart: f64, next_zed: f64, next_twitch: f64, since: f64,
    cur_l: Eye, cur_r: Eye, smile: f32,
    rng: u64,
}

impl Sim {
    pub fn new(pack: Pack, emotion: &str, now: f64) -> Sim {
        let look = pack.looks[emotion].clone();
        let mut s = Sim {
            pack, buf: [0; W * H], emotion: String::new(), asleep: false, talking: false, mouth_level: 0., mouth: 0.,
            blink: 0., blink_closing: false, gaze_x: 0., gaze_y: 0., gtx: 0., gty: 0.,
            pupil_x: 0., pupil_y: 0., ptx: 0., pty: 0., pop: 0., squint: 0., tear_y: -1., think_dots: 0, jitter: 0.,
            hearts: Default::default(), heart_side: false, zeds: Default::default(), breath: 0., twitch: 0., frame: 0,
            next_blink: 0., next_saccade: 0., next_squint: 0., next_pupil: 0., next_tear: 0., next_dot: 0.,
            next_heart: 0., next_zed: now + 800., next_twitch: 0., since: now,
            cur_l: look.l, cur_r: look.r, smile: look.smile, rng: 0x9E37_79B9_7F4A_7C15 ^ now.to_bits(),
        };
        s.next_blink = now + s.rnd(800, 4000) as f64;
        s.next_saccade = now + s.rnd(500, 2500) as f64;
        s.next_squint = now + s.rnd(4000, 12000) as f64;
        s.next_pupil = now + s.rnd(300, 1500) as f64;
        s.next_twitch = now + s.rnd(5000, 9000) as f64;
        s.set_emotion(emotion, now);
        s.pop = 0.;
        s
    }

    /// Arduino's random(a, b): a <= n < b.
    fn rnd(&mut self, a: i32, b: i32) -> i32 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        a + (self.rng % (b - a) as u64) as i32
    }

    fn look(&self) -> &Look {
        &self.pack.looks[&self.emotion]
    }

    pub fn set_pack(&mut self, pack: Pack, now: f64) {
        self.pack = pack;
        let e = std::mem::take(&mut self.emotion);
        self.set_emotion(&e, now);
    }

    pub fn set_emotion(&mut self, e: &str, now: f64) {
        if !self.pack.looks.contains_key(e) {
            return;
        }
        if e != self.emotion {
            self.pop = 1.;
            self.since = now;
            self.tear_y = -1.;
            self.next_tear = now + 1500.;
            self.think_dots = 0;
            for h in &mut self.hearts {
                h.alive = false;
            }
            self.next_heart = now + 200.;
        }
        self.emotion = e.to_string();
        let (gx, gy) = (self.look().gaze_x, self.look().gaze_y);
        self.gtx = gx;
        self.gty = gy;
    }

    pub fn set_asleep(&mut self, on: bool, now: f64) {
        if on == self.asleep {
            return;
        }
        self.asleep = on;
        if on {
            for z in &mut self.zeds {
                z.alive = false;
            }
            self.next_zed = now + 800.;
            self.next_twitch = now + self.rnd(5000, 9000) as f64;
            self.gtx = 0.;
            self.gty = 0.;
        } else {
            self.pop = 1.;
            self.blink = 0.;
            let e = self.emotion.clone();
            self.set_emotion(&e, now);
        }
    }

    pub fn step(&mut self, now: f64) {
        self.frame += 1;
        let look = self.look().clone();
        let f = if self.asleep { 0.12 } else { 0.25 };
        let (tl, tr) = if self.asleep { (self.pack.asleep, self.pack.asleep) } else { (look.l, look.r) };
        for i in 0..10 {
            self.cur_l[i] = approach(self.cur_l[i], tl[i], f);
            self.cur_r[i] = approach(self.cur_r[i], tr[i], f);
        }
        self.smile = approach(self.smile, if self.asleep { 0. } else { look.smile }, f);
        self.pop *= 0.82;
        self.squint *= 0.85;

        if self.asleep {
            self.breath += 0.035;
            self.blink = 0.;
            self.blink_closing = false;
            self.twitch *= 0.8;
            if now >= self.next_twitch {
                self.twitch = 1.;
                self.next_twitch = now + self.rnd(6000, 12000) as f64;
            }
            if now >= self.next_zed {
                let x = if self.pack.cyclops { WF / 2. + self.cur_l[EW] / 2. + 2. }
                        else { WF / 2. + self.pack.gap / 2. + self.cur_l[EW] + 2. };
                if let Some(z) = self.zeds.iter_mut().find(|z| !z.alive) {
                    *z = Particle { alive: true, x: x.min(108.), y: HF / 2. - 6., age: 0. };
                }
                self.next_zed = now + self.rnd(1100, 1700) as f64;
            }
            for z in self.zeds.iter_mut().filter(|z| z.alive) {
                z.age += 0.012;
                z.y -= 0.32;
                z.x += 0.22;
                if z.age >= 1. || z.y < -12. {
                    z.alive = false;
                }
            }
            self.gaze_x = approach(self.gaze_x, 0., 0.2);
            self.gaze_y = approach(self.gaze_y, 0., 0.2);
            self.mouth = approach(self.mouth, 0., 0.3);
            return;
        }

        if self.blink_closing {
            self.blink += 0.45;
            if self.blink >= 1. {
                self.blink = 1.;
                self.blink_closing = false;
            }
        } else if self.blink > 0. {
            self.blink = (self.blink - 0.28).max(0.);
        }

        if now >= self.next_blink {
            self.blink_closing = true;
            self.next_blink = now + self.rnd(2200, 6000) as f64;
            if self.rnd(0, 100) < 20 {
                self.next_blink = now + 400.;
            }
        }
        if now >= self.next_saccade && look.gaze_x == 0. && look.gaze_y == 0. {
            self.gtx = self.rnd(-8, 9) as f32;
            self.gty = self.rnd(-4, 5) as f32;
            if self.rnd(0, 100) < 40 {
                self.gtx = 0.;
                self.gty = 0.;
            }
            self.next_saccade = now + self.rnd(1200, 4000) as f64;
        }
        if now >= self.next_pupil {
            if self.rnd(0, 100) < 35 {
                self.ptx = self.rnd(-25, 26) as f32 / 100.;
                self.pty = self.rnd(-20, 21) as f32 / 100.;
            } else {
                self.ptx = self.rnd(-90, 91) as f32 / 100.;
                self.pty = self.rnd(-60, 61) as f32 / 100.;
            }
            self.next_pupil = now + self.rnd(600, 2600) as f64;
        }
        if now >= self.next_squint && self.emotion == "neutral" {
            self.squint = 1.;
            self.next_squint = now + self.rnd(8000, 16000) as f64;
        }

        if look.fx.contains("tear") {
            if self.tear_y < 0. && now >= self.next_tear {
                self.tear_y = 0.;
            }
            if self.tear_y >= 0. {
                self.tear_y += 0.7;
                if self.tear_y > HF {
                    self.tear_y = -1.;
                    self.next_tear = now + self.rnd(2500, 5000) as f64;
                }
            }
        }
        if look.fx.contains("dots") && now >= self.next_dot {
            self.think_dots = (self.think_dots + 1) % 4;
            self.next_dot = now + 420.;
        }
        if look.fx.contains("hearts") && now >= self.next_heart {
            if let Some(i) = self.hearts.iter().position(|h| !h.alive) {
                self.heart_side = !self.heart_side;
                let (p, side) = (&self.pack, self.heart_side);
                let x = if p.cyclops {
                    if side { WF / 2. + self.cur_l[EW] / 2. + 3. } else { WF / 2. - self.cur_l[EW] / 2. - 10. }
                } else if side { WF / 2. + p.gap / 2. + self.cur_r[EW] + 2. } else { WF / 2. - p.gap / 2. - self.cur_l[EW] - 9. };
                self.hearts[i] = Particle { alive: true, x, y: HF / 2. + 6., age: 0. };
            }
            self.next_heart = now + self.rnd(600, 1000) as f64;
        }
        for h in self.hearts.iter_mut().filter(|h| h.alive) {
            h.age += 0.015;
            h.y -= 0.45;
            if h.age >= 1. || h.y < -6. {
                h.alive = false;
            }
        }
        self.jitter = if look.fx.contains("shake") && self.frame % 3 == 0 { self.rnd(-1, 2) as f32 } else { 0. };

        let mouth_goal = if self.talking { clamp(0.15 + self.mouth_level * 4., 0.15, 1.) } else { 0. };
        self.mouth = approach(self.mouth, mouth_goal, if self.talking { 0.5 } else { 0.3 });
        self.gaze_x = approach(self.gaze_x, self.gtx, 0.35);
        self.gaze_y = approach(self.gaze_y, self.gty, 0.35);
        self.pupil_x = approach(self.pupil_x, self.ptx, 0.5);
        self.pupil_y = approach(self.pupil_y, self.pty, 0.5);
    }

    // ── pixels ──
    fn dot(&mut self, x: i32, y: i32, c: u8) {
        if x >= 0 && y >= 0 && (x as usize) < W && (y as usize) < H {
            self.buf[y as usize * W + x as usize] = c;
        }
    }

    fn rect(&mut self, x: i32, y: i32, w: i32, h: i32, c: u8) {
        for j in 0..h {
            for i in 0..w {
                self.dot(x + i, y + j, c);
            }
        }
    }

    fn round_box(&mut self, x: i32, y: i32, w: i32, h: i32, r: i32, c: u8) {
        let (wf, hf) = (w as f32, h as f32);
        let r = (r as f32).min(wf.min(hf) * 0.5);
        for j in 0..h {
            for i in 0..w {
                let (px, py) = (i as f32 + 0.5, j as f32 + 0.5);
                let (qx, qy) = (clamp(px, r, wf - r), clamp(py, r, hf - r));
                if (px - qx) * (px - qx) + (py - qy) * (py - qy) <= r * r {
                    self.dot(x + i, y + j, c);
                }
            }
        }
    }

    fn line(&mut self, mut x0: i32, mut y0: i32, x1: i32, y1: i32) {
        let (dx, sx) = ((x1 - x0).abs(), if x0 < x1 { 1 } else { -1 });
        let (dy, sy) = (-(y1 - y0).abs(), if y0 < y1 { 1 } else { -1 });
        let mut err = dx + dy;
        loop {
            self.dot(x0, y0, 1);
            if x0 == x1 && y0 == y1 {
                break;
            }
            let e2 = 2 * err;
            if e2 >= dy {
                err += dy;
                x0 += sx;
            }
            if e2 <= dx {
                err += dx;
                y0 += sy;
            }
        }
    }

    fn disc(&mut self, cx: i32, cy: i32, r: i32) {
        for j in -r..=r {
            for i in -r..=r {
                if i * i + j * j <= r * r + r {
                    self.dot(cx + i, cy + j, 1);
                }
            }
        }
    }

    fn glyph(&mut self, rows: &[&str], x: i32, y: i32) {
        for (j, row) in rows.iter().enumerate() {
            for (i, ch) in row.bytes().enumerate() {
                if ch == b'#' {
                    self.dot(x + i as i32, y + j as i32, 1);
                }
            }
        }
    }

    fn draw_eye(&mut self, e: Eye, cx: f32, cy: f32, inner: f32, h_scale: f32) {
        let w = e[EW];
        let h = (e[EH] * h_scale * (1. - self.blink)).max(2.);
        let gcx = cx - inner * e[DX];
        let ecy = cy + e[DY];
        let g = Geo {
            cx: gcx, w, st: e[ST], sb: e[SB], lid: e[LID], cheek: e[CHEEK], inner,
            left: gcx - w * 0.5, right: gcx + w * 0.5, top: ecy - h * 0.5, bot: ecy + h * 0.5,
            rt: e[RT].min(w.min(h) * 0.5).max(0.), rb: e[RB].min(w.min(h) * 0.5).max(0.),
        };
        let (x0, x1) = (g.left.floor() as i32, g.right.ceil() as i32);
        let (y0, y1) = (g.top.floor() as i32, g.bot.ceil() as i32);
        let outline = self.pack.outline;
        for y in y0..y1 {
            for x in x0..x1 {
                let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
                let mut on = in_eye(&g, px, py);
                if on && outline {
                    on = !(in_eye(&g, px - 2., py) && in_eye(&g, px + 2., py)
                        && in_eye(&g, px, py - 2.) && in_eye(&g, px, py + 2.));
                }
                if on {
                    self.dot(x, y, 1);
                }
            }
        }
        if self.pack.glint && h > 12. && !self.asleep {
            self.draw_pupil(&g, x0, x1, y0, y1);
        }
    }

    fn draw_pupil(&mut self, g: &Geo, x0: i32, x1: i32, y0: i32, y1: i32) {
        let r = clamp(g.w * 0.14, 3., 6.);
        let rows: Vec<i32> = (y0..y1).filter(|&y| in_eye(g, g.cx, y as f32 + 0.5)).collect();
        let (Some(&top), Some(&bot)) = (rows.first(), rows.last()) else { return };
        let vcy = (top + bot + 1) as f32 * 0.5;
        let cols: Vec<i32> = (x0..x1).filter(|&x| in_eye(g, x as f32 + 0.5, vcy)).collect();
        let (Some(&left), Some(&right)) = (cols.first(), cols.last()) else { return };
        let vcx = (left + right + 1) as f32 * 0.5;
        let room_x = ((right - left + 1) as f32 * 0.5 - r - 2.).max(0.);
        let room_y = ((bot - top + 1) as f32 * 0.5 - r - 1.5).max(0.);
        let px = clamp(self.pupil_x + self.gaze_x / 12., -1., 1.);
        let py = clamp(self.pupil_y + self.gaze_y / 8., -1., 1.);
        let (pcx, pcy) = (vcx + px * room_x, vcy + py * room_y);
        let ink = if self.pack.outline { 1 } else { 0 };
        let ri = r.ceil() as i32;
        let (fx, fy) = (pcx.floor() as i32, pcy.floor() as i32);
        for y in fy - ri..=fy + ri {
            for x in fx - ri..=fx + ri {
                let (dx, dy) = (x as f32 + 0.5 - pcx, y as f32 + 0.5 - pcy);
                if dx * dx + dy * dy <= r * r && in_eye(g, x as f32 + 0.5, y as f32 + 0.5) {
                    self.dot(x, y, ink);
                }
            }
        }
        if !self.pack.outline && r >= 4. {
            let (hx, hy) = ((pcx - r * 0.5).floor() as i32, (pcy - r * 0.55).floor() as i32);
            if in_eye(g, hx as f32 + 0.5, hy as f32 + 0.5) {
                self.rect(hx, hy, 2, 2, 1);
            }
        }
    }

    fn draw_mouth(&mut self, cx: i32, cy: f32) {
        let my0 = (cy + self.cur_l[EH] * 0.5).floor() as i32;
        let hgt = H as i32;
        if self.mouth >= 0.05 && self.pack.mouth != 2 {
            let mh = 2 + (self.mouth * 9.).floor() as i32;
            let mw = 20 + (self.mouth * 8.).floor() as i32;
            let my = (my0 + 4).min(hgt - 1 - mh);
            self.round_box(cx - mw / 2, my, mw, mh, (mh / 2).min(4), 1);
            if mh > 6 {
                self.round_box(cx - mw / 2 + 3, my + 2, mw - 6, mh - 4, 2, 0);
            }
        } else if self.pack.mouth == 1 {
            let (mw, my) = (14, (my0 + 6).min(hgt - 4));
            for i in 0..=mw {
                let t = (i as f32 - mw as f32 * 0.5) / (mw as f32 * 0.5);
                let yy = (my as f32 + self.smile * (1. - t * t) + 0.5).floor() as i32;
                self.dot(cx - mw / 2 + i, yy, 1);
                self.dot(cx - mw / 2 + i, yy + 1, 1);
            }
        }
    }

    fn draw_flourishes(&mut self, left_outer: f32, right_outer: f32, eye_top: i32, eye_bottom: i32, now: f64) {
        let fx = self.look().fx.clone();
        let since = now - self.since;
        let (lo, ro) = (left_outer.floor() as i32, right_outer.floor() as i32);
        let wi = W as i32;
        if fx.contains("pop") && since < 900. {
            self.glyph(&BANG, 116, 4);
        }
        if fx.contains("blush") {
            for i in 0..2 {
                let (lx, rx) = (lo - 8 + i * 3, ro + 3 + i * 3);
                self.line(lx, eye_bottom + 1, lx + 3, eye_bottom - 3);
                self.line(rx, eye_bottom + 1, rx + 3, eye_bottom - 3);
            }
        }
        if fx.contains("tear") && self.tear_y >= 0. {
            let (tx, ty) = (ro - 3, eye_bottom + self.tear_y.floor() as i32);
            self.disc(tx, ty, 2);
            self.line(tx, ty - 5, tx, ty - 2);
        }
        if fx.contains("dots") {
            for i in 0..self.think_dots {
                self.disc(wi - 26 + i * 8, 9, 2);
            }
        }
        if fx.contains("steam") && (self.frame / 6) % 2 == 0 {
            self.line(lo - 4, eye_top - 4, lo - 2, eye_top - 9);
            self.line(ro + 4, eye_top - 4, ro + 2, eye_top - 9);
        }
        if fx.contains("hearts") {
            for h in self.hearts {
                if !h.alive || (h.age > 0.8 && self.frame % 2 == 0) {
                    continue;
                }
                self.glyph(&HEART, h.x.floor() as i32, h.y.floor() as i32);
            }
        }
        if fx.contains("question") {
            self.glyph(&QUESTION, 112, 3 + ((self.frame as f32 * 0.15).sin() * 2.).floor() as i32);
        }
        if fx.contains("sparkle") {
            let a = (self.frame / 5) % 2 == 0;
            let pts = [
                if a { lo - 6 } else { lo - 5 }, if a { eye_top + 3 } else { eye_bottom - 5 },
                if a { ro + 6 } else { ro + 5 }, if a { eye_bottom - 4 } else { eye_top + 2 },
            ];
            for k in 0..2 {
                let (x, y) = (pts[k * 2], pts[k * 2 + 1]);
                self.line(x - 2, y, x + 2, y);
                self.line(x, y - 2, x, y + 2);
            }
        }
    }

    fn draw_zeds(&mut self) {
        for z in self.zeds {
            if !z.alive || (z.age > 0.8 && self.frame % 2 == 0) {
                continue;
            }
            let s = if z.age < 0.33 { 4 } else if z.age < 0.66 { 6 } else { 8 };
            let (x, y) = (z.x.floor() as i32, z.y.floor() as i32 - s);
            if x < 0 || x >= W as i32 - 4 || y < 0 {
                continue;
            }
            for b in 0..if s >= 8 { 2 } else { 1 } {
                self.line(x + b, y, x + s + b, y);
                self.line(x + s + b, y, x + b, y + s);
                self.line(x + b, y + s, x + s + b, y + s);
            }
        }
    }

    pub fn render(&mut self, now: f64) {
        self.buf.fill(0);
        let look = self.look().clone();
        let lively = !self.asleep;
        let mut bob = if self.asleep { self.breath.sin() * 2. } else { -self.mouth * 2. };
        if lively && look.fx.contains("bounce") {
            bob -= (self.frame as f32 * 0.18).sin().abs() * 3.;
        }
        let sway = if lively && look.fx.contains("wobble") { (self.frame as f32 * 0.08).sin() * 3. } else { 0. };
        let mut cy = HF / 2. + self.pack.y + self.gaze_y + bob;
        if self.talking {
            cy -= 2.;
        }
        let gx = self.gaze_x + self.jitter + sway;
        let mut h_scale = 1. + self.pop * look.pop;
        h_scale *= 1. - self.squint * 0.4;
        if self.asleep {
            h_scale += self.twitch * 2.5;
        }
        let (l, r) = (self.cur_l, self.cur_r);
        let (left_outer, right_outer) = if self.pack.cyclops {
            let cx = WF / 2. + gx;
            self.draw_eye(l, cx, cy, 1., h_scale);
            (cx - l[DX] - l[EW] / 2., cx - l[DX] + l[EW] / 2.)
        } else {
            let lcx = WF / 2. - self.pack.gap / 2. - l[EW] / 2. + gx;
            let rcx = WF / 2. + self.pack.gap / 2. + r[EW] / 2. + gx;
            self.draw_eye(l, lcx, cy, 1., h_scale);
            self.draw_eye(r, rcx, cy, -1., h_scale);
            (lcx - l[DX] - l[EW] / 2., rcx + r[DX] + r[EW] / 2.)
        };
        let eye_top = (cy - l[EH] * h_scale / 2.).floor() as i32;
        let eye_bottom = (cy + l[EH] * h_scale / 2.).floor() as i32;
        if self.asleep {
            self.draw_zeds();
        } else {
            self.draw_flourishes(left_outer, right_outer, eye_top, eye_bottom, now);
            self.draw_mouth(W as i32 / 2 + (self.gaze_x * 0.5).floor() as i32, cy.floor());
        }
    }
}
