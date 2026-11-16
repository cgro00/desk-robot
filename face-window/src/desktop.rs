// Plain X11 (EWMH) instead of asking one window manager: works under i3, GNOME, KDE, Xfce, ...

use x11rb::connection::Connection;
use x11rb::protocol::xproto::{AtomEnum, ClientMessageEvent, ConnectionExt as _, EventMask, PropMode, Window};
use x11rb::rust_connection::RustConnection;
use x11rb::wrapper::ConnectionExt as _;

pub struct Desktop {
    conn: RustConnection,
    root: Window,
}

impl Desktop {
    pub fn connect() -> Option<Desktop> {
        let (conn, screen) = x11rb::connect(None).ok()?;
        let root = conn.setup().roots[screen].root;
        Some(Desktop { conn, root })
    }

    /// The mouse on the whole screen, asked each time: the window's own (relative) mouse position lags a frame
    /// behind its moves, which made dragging shake.
    pub fn pointer(&self) -> Option<(isize, isize)> {
        let r = self.conn.query_pointer(self.root).ok()?.reply().ok()?;
        Some((r.root_x as isize, r.root_y as isize))
    }

    fn atom(&self, name: &str) -> Option<u32> {
        Some(self.conn.intern_atom(false, name.as_bytes()).ok()?.reply().ok()?.atom)
    }

    /// Ask the window manager to float the window, keep it above others and on every workspace, leave it out of
    /// taskbars and never give it keyboard focus. Window managers read these when a window is mapped, so it is
    /// unmapped and mapped again.
    pub fn pin(&self, window: Window) -> Option<()> {
        let atoms = |names: &[&str]| names.iter().map(|n| self.atom(n)).collect::<Option<Vec<u32>>>();
        let [wm_type, utility, state] = atoms(&["_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_UTILITY", "_NET_WM_STATE"])?[..]
        else { return None };
        let states = atoms(&["_NET_WM_STATE_STICKY", "_NET_WM_STATE_ABOVE", "_NET_WM_STATE_SKIP_TASKBAR",
                             "_NET_WM_STATE_SKIP_PAGER"])?;
        self.conn.unmap_window(window).ok()?;
        self.conn.get_input_focus().ok()?.reply().ok()?;   // the unmap is through before the hints change
        self.conn.change_property32(PropMode::REPLACE, window, wm_type, AtomEnum::ATOM, &[utility]).ok()?;
        self.conn.change_property32(PropMode::REPLACE, window, state, AtomEnum::ATOM, &states).ok()?;
        self.conn.change_property8(PropMode::REPLACE, window, AtomEnum::WM_CLASS, AtomEnum::STRING,
                                   b"rocky-face\0Rocky\0").ok()?;
        // WM_HINTS with the input flag off: never takes keyboard focus, not even when clicked
        self.conn.change_property32(PropMode::REPLACE, window, AtomEnum::WM_HINTS, AtomEnum::WM_HINTS,
                                    &[1, 0, 0, 0, 0, 0, 0, 0, 0]).ok()?;
        // and a user time of 0: "don't focus me when I appear"
        if let Some(user_time) = self.atom("_NET_WM_USER_TIME") {
            self.conn.change_property32(PropMode::REPLACE, window, user_time, AtomEnum::CARDINAL, &[0]).ok()?;
        }
        self.conn.map_window(window).ok()?;
        // Some (i3) only take sticky / above as a request once the window is managed.
        for &st in &states[..2] {
            let msg = ClientMessageEvent::new(32, window, state, [1, st, 0, 1, 0]);
            self.conn.send_event(false, self.root, EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY, msg).ok()?;
        }
        self.conn.flush().ok()
    }
}
