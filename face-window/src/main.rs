mod desktop;


use rocky_face_core::face::{self, Sim, H, W};
use minifb::{MouseButton, Scale, Window, WindowOptions};
use serde_json::Value;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const ON: u32 = 0xF2B45C;   // the OLED's amber, as the dashboard paints it
const OFF: u32 = 0x0A0D14;
const BUILTIN_PACK: &str = include_str!("../../server/faces/rocky.json");

#[derive(Default)]
struct Shared {
    status: Option<Status>,
    pack: Option<Value>,
}

struct Status {
    emotion: String,
    awake: bool,
    speaking: bool,
}

fn get_json(agent: &ureq::Agent, url: &str) -> Option<Value> {
    let body = agent.get(url).call().ok()?.body_mut().read_to_string().ok()?;
    serde_json::from_str(&body).ok()
}

/// The active pack from the launcher (it knows custom packs and which one is picked); None if it isn't running.
fn active_pack(j: &Value) -> Option<Value> {
    let packs = j.get("packs")?.as_array()?;
    let active = j.get("active").and_then(Value::as_str).unwrap_or("rocky");
    packs.iter().find(|p| p.get("id").and_then(Value::as_str) == Some(active))?.get("pack").cloned()
}

fn poll(shared: Arc<Mutex<Shared>>, brain: String, launcher: String) {
    let agent: ureq::Agent = ureq::Agent::config_builder().timeout_global(Some(Duration::from_millis(800))).build().into();
    let mut last_pack_check = Instant::now() - Duration::from_secs(60);
    loop {
        let status = get_json(&agent, &format!("{brain}/status")).map(|s| Status {
            emotion: s.get("emotion").and_then(Value::as_str).unwrap_or("neutral").to_string(),
            awake: s.get("awake").and_then(Value::as_bool).unwrap_or(false),
            speaking: s.get("speaking").and_then(Value::as_bool).unwrap_or(false),
        });
        let pack = if last_pack_check.elapsed() > Duration::from_secs(5) {
            last_pack_check = Instant::now();
            get_json(&agent, &format!("{launcher}/launcher/faces")).as_ref().and_then(active_pack)
        } else {
            None
        };
        let mut s = shared.lock().unwrap();
        s.status = status;
        if pack.is_some() {
            s.pack = pack;
        }
        drop(s);
        thread::sleep(Duration::from_millis(150));
    }
}

fn post(url: &str, body: &str) {
    let sent = ureq::post(url).header("X-Rocky-Console", "1").header("Content-Type", "application/json").send(body);
    if let Err(e) = sent {
        eprintln!("rocky-face: {url} failed ({e}); is launcher.py running?");
    }
}

fn x11_window(window: &Window) -> Option<u32> {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    match window.window_handle().ok()?.as_raw() {
        RawWindowHandle::Xlib(h) => Some(h.window as u32),
        RawWindowHandle::Xcb(h) => Some(h.window.get()),
        _ => None,
    }
}

fn arg(name: &str) -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}

fn main() {
    let brain = arg("--brain").unwrap_or_else(|| "http://127.0.0.1:8767".into());
    let launcher = arg("--launcher").unwrap_or_else(|| "http://127.0.0.1:8766".into());
    let (scale, factor) = match arg("--scale").as_deref() {
        Some("1") => (Scale::X1, 1),
        Some("4") => (Scale::X4, 4),
        Some("8") => (Scale::X8, 8),
        _ => (Scale::X2, 2),
    };
    let margin: isize = arg("--margin").and_then(|m| m.parse().ok()).unwrap_or(24);

    let rocky: Value = serde_json::from_str(BUILTIN_PACK).expect("built-in pack");
    let fallback = face::resolve(&rocky, None);
    let start = Instant::now();
    let now = || start.elapsed().as_secs_f64() * 1000.;
    let mut sim = Sim::new(fallback.clone(), "neutral", now());
    sim.set_asleep(true, now());

    let shared = Arc::new(Mutex::new(Shared::default()));
    {
        let (shared, brain, launcher) = (shared.clone(), brain.clone(), launcher.clone());
        thread::spawn(move || poll(shared, brain, launcher));
    }

    let desktop = desktop::Desktop::connect();
    let was_active = desktop.as_ref().and_then(desktop::Desktop::active_window);
    let mut window = Window::new("Rocky", W, H, WindowOptions {
        borderless: true,
        topmost: true,
        scale,
        ..WindowOptions::default()
    })
    .expect("open window");
    window.set_target_fps(30);
    let me = x11_window(&window);
    if let Some(d) = &desktop {
        if let Some(id) = me {
            d.pin(id);
        }
        if let Some(w) = was_active {
            d.activate(w);   // window managers that focus a new window anyway: give it straight back
        }
        if let Some((x, y, w, _)) = d.usable_area() {
            window.set_position(x + w - (W * factor) as isize - margin, y + 4 * margin);
        }
    }

    let mut pixels = vec![OFF; W * H];
    let mut shown_pack: Option<Value> = None;
    let mut grab: Option<((isize, isize), (isize, isize))> = None;   // window and pointer where the press began
    let mut last_other = was_active;

    while window.is_open() {
        let t = now();
        {
            let s = shared.lock().unwrap();
            if s.pack.is_some() && s.pack != shown_pack {
                shown_pack = s.pack.clone();
                sim.set_pack(face::resolve(shown_pack.as_ref().unwrap(), Some(&fallback)), t);
            }
            match &s.status {
                Some(st) => {
                    sim.set_emotion(&st.emotion, t);
                    sim.set_asleep(st.emotion == "sleepy", t);
                    sim.talking = st.speaking;
                }
                None => {
                    sim.set_asleep(true, t);
                    sim.talking = false;
                }
            }
        }
        if sim.talking {
            sim.mouth_level = ((t / 110.).sin() * 0.18 + (t / 47.).sin() * 0.06).max(0.) as f32;
        }
        sim.step(t);
        sim.render(t);
        for (p, &on) in pixels.iter_mut().zip(sim.buf.iter()) {
            *p = if on != 0 { ON } else { OFF };
        }

        // drag the borderless window with the left button; a click that didn't move it starts a stopped brain, else wakes him or puts him to sleep
        // focus that lands here anyway (click to focus, focus follows mouse) goes straight back where it was
        if let (Some(d), Some(me)) = (&desktop, me) {
            match d.active_window() {
                Some(w) if w == me => {
                    if let Some(o) = last_other {
                        d.activate(o);
                    }
                }
                Some(w) => last_other = Some(w),
                None => {}
            }
        }

        let at = desktop.as_ref().and_then(desktop::Desktop::pointer);
        match (window.get_mouse_down(MouseButton::Left), at) {
            (true, Some(p)) => match grab {
                None => grab = Some((window.get_position(), p)),
                Some((win, start)) => window.set_position(win.0 + p.0 - start.0, win.1 + p.1 - start.1),
            },
            _ => {
                let moved = match (grab, at) {
                    (Some((_, start)), Some(p)) => (p.0 - start.0).abs() + (p.1 - start.1).abs(),
                    _ => isize::MAX,
                };
                if moved < 4 {
                    let awake = shared.lock().unwrap().status.as_ref().map(|s| s.awake);
                    let (url, body) = match awake {
                        None => (format!("{launcher}/launcher/start"), "{}"),
                        Some(true) => (format!("{brain}/api/sleep"), r#"{"on": true}"#),
                        Some(false) => (format!("{brain}/api/sleep"), r#"{"on": false}"#),
                    };
                    thread::spawn(move || post(&url, body));
                }
                grab = None;
            }
        }

        window.update_with_buffer(&pixels, W, H).expect("draw");
    }
}
