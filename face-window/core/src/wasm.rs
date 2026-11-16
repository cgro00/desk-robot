// Plain exports for the dashboard (FaceKit in server/brain/dashboard.html): no wasm-bindgen, so no generated glue.
// Packs and sims live here and the page holds their ids; strings come in through `alloc`, results go out through
// a pointer plus `out_len`.

use crate::face::{self, Pack, Sim, EMOTIONS};
use std::cell::RefCell;

thread_local! {
    static PACKS: RefCell<Vec<Option<Pack>>> = const { RefCell::new(Vec::new()) };
    static SIMS: RefCell<Vec<Option<Box<Sim>>>> = const { RefCell::new(Vec::new()) };
    static FALLBACK: RefCell<Option<Pack>> = const { RefCell::new(None) };
    static OUT: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}

const NONE: u32 = u32::MAX;

fn store<T>(list: &mut Vec<Option<T>>, item: T) -> u32 {
    match list.iter().position(Option::is_none) {
        Some(i) => {
            list[i] = Some(item);
            i as u32
        }
        None => {
            list.push(Some(item));
            (list.len() - 1) as u32
        }
    }
}

fn free<T>(list: &mut [Option<T>], id: u32) {
    if let Some(slot) = list.get_mut(id as usize) {
        *slot = None;
    }
}

fn emotion(i: u32) -> Option<&'static str> {
    EMOTIONS.get(i as usize).copied()
}

fn pack(id: u32) -> Option<Pack> {
    PACKS.with_borrow(|p| p.get(id as usize).cloned().flatten())
}

fn with_sim(id: u32, f: impl FnOnce(&mut Sim)) {
    SIMS.with_borrow_mut(|s| {
        if let Some(Some(sim)) = s.get_mut(id as usize) {
            f(sim);
        }
    });
}

#[no_mangle]
pub extern "C" fn alloc(len: usize) -> *mut u8 {
    let mut buf = Vec::<u8>::with_capacity(len);
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    ptr
}

/// A pack from its JSON (which `alloc` handed out; freed here), resolved against the fallback. Its id.
#[no_mangle]
pub extern "C" fn pack_new(ptr: *mut u8, len: usize) -> u32 {
    let bytes = unsafe { Vec::from_raw_parts(ptr, len, len) };
    let value = serde_json::from_slice(&bytes).unwrap_or_default();
    let resolved = FALLBACK.with_borrow(|fb| face::resolve(&value, fb.as_ref()));
    PACKS.with_borrow_mut(|p| store(p, resolved))
}

#[no_mangle]
pub extern "C" fn pack_free(id: u32) {
    PACKS.with_borrow_mut(|p| free(p, id));
}

/// What later packs take for anything they leave out (NONE = the built-in defaults).
#[no_mangle]
pub extern "C" fn pack_set_fallback(id: u32) {
    let fb = if id == NONE { None } else { pack(id) };
    FALLBACK.with_borrow_mut(|f| *f = fb);
}

/// The resolved pack as JSON (read `out_len` bytes from the returned pointer before the next call).
#[no_mangle]
pub extern "C" fn pack_json(id: u32) -> *const u8 {
    let json = pack(id).map(|p| p.to_json().to_string()).unwrap_or_default();
    OUT.with_borrow_mut(|o| {
        *o = json.into_bytes();
        o.as_ptr()
    })
}

#[no_mangle]
pub extern "C" fn out_len() -> usize {
    OUT.with_borrow(Vec::len)
}

#[no_mangle]
pub extern "C" fn sim_new(pack_id: u32, emo: u32, now: f64) -> u32 {
    let Some(p) = pack(pack_id) else { return NONE };
    let sim = Box::new(Sim::new(p, emotion(emo).unwrap_or("neutral"), now));
    SIMS.with_borrow_mut(|s| store(s, sim))
}

#[no_mangle]
pub extern "C" fn sim_free(id: u32) {
    SIMS.with_borrow_mut(|s| free(s, id));
}

#[no_mangle]
pub extern "C" fn sim_set_pack(id: u32, pack_id: u32, now: f64) {
    if let Some(p) = pack(pack_id) {
        with_sim(id, |s| s.set_pack(p, now));
    }
}

#[no_mangle]
pub extern "C" fn sim_set_emotion(id: u32, emo: u32, now: f64) {
    if let Some(e) = emotion(emo) {
        with_sim(id, |s| s.set_emotion(e, now));
    }
}

#[no_mangle]
pub extern "C" fn sim_set_still(id: u32, emo: u32, now: f64) {
    if let Some(e) = emotion(emo) {
        with_sim(id, |s| s.set_still(e, now));
    }
}

#[no_mangle]
pub extern "C" fn sim_set_asleep(id: u32, on: u32, now: f64) {
    with_sim(id, |s| s.set_asleep(on != 0, now));
}

#[no_mangle]
pub extern "C" fn sim_set_talking(id: u32, on: u32, level: f32) {
    with_sim(id, |s| {
        s.talking = on != 0;
        s.mouth_level = level;
    });
}

#[no_mangle]
pub extern "C" fn sim_step(id: u32, now: f64) {
    with_sim(id, |s| s.step(now));
}

/// Draws a frame; the W*H bytes (0 / 1 per pixel) at the returned pointer (copy them before the next call).
#[no_mangle]
pub extern "C" fn sim_render(id: u32, now: f64) -> *const u8 {
    let mut ptr = std::ptr::null();
    with_sim(id, |s| {
        s.render(now);
        ptr = s.buf.as_ptr();
    });
    ptr
}
