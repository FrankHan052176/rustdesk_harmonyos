// OpenHarmony controlled-side input injection and cursor position reporting.
//
// Uses `libohinput.so` resolved at runtime via `dlopen`/`dlsym` so the binary
// loads cleanly across API levels and degrades gracefully when ACL permissions
// (`ohos.permission.CONTROL_DEVICE` / `ohos.permission.INPUT_MONITORING`) are
// not granted on the current device or signing profile.

#![allow(non_camel_case_types)]

use super::*;
use crate::common::input::{
    MOUSE_BUTTON_BACK, MOUSE_BUTTON_FORWARD, MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT,
    MOUSE_BUTTON_WHEEL, MOUSE_TYPE_DOWN, MOUSE_TYPE_MASK, MOUSE_TYPE_MOVE,
    MOUSE_TYPE_MOVE_RELATIVE, MOUSE_TYPE_TRACKPAD, MOUSE_TYPE_UP, MOUSE_TYPE_WHEEL,
};
use hbb_common::get_time;
use std::collections::HashSet;
use std::ffi::{c_char, c_int, c_void};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::{Mutex, OnceLock};

pub const NAME_CURSOR: &'static str = "";
pub const NAME_POS: &'static str = "mouse_pos";
pub const NAME_WINDOW_FOCUS: &'static str = "";

const INVALID_CURSOR_POS: i32 = i32::MIN;
const RTLD_NOW: c_int = 2;
const RTLD_LOCAL: c_int = 0;
const CLOCK_MONOTONIC: c_int = 1;

// Input_InjectionStatus
const INJECTION_UNAUTHORIZED: i32 = 0;
const INJECTION_AUTHORIZED: i32 = 2;

// Input_KeyEventAction
const KEY_ACTION_DOWN: i32 = 1;
const KEY_ACTION_UP: i32 = 2;

// Input_MouseEventAction
const MOUSE_ACTION_MOVE: i32 = 1;
const MOUSE_ACTION_BUTTON_DOWN: i32 = 2;
const MOUSE_ACTION_BUTTON_UP: i32 = 3;
const MOUSE_ACTION_AXIS_BEGIN: i32 = 4;
const MOUSE_ACTION_AXIS_UPDATE: i32 = 5;
const MOUSE_ACTION_AXIS_END: i32 = 6;

// InputEvent_MouseAxis
const MOUSE_AXIS_SCROLL_VERTICAL: i32 = 0;
const MOUSE_AXIS_SCROLL_HORIZONTAL: i32 = 1;

// Input_MouseEventButton
const OH_MOUSE_BUTTON_NONE: i32 = -1;
const OH_MOUSE_BUTTON_LEFT: i32 = 0;
const OH_MOUSE_BUTTON_MIDDLE: i32 = 1;
const OH_MOUSE_BUTTON_RIGHT: i32 = 2;
const OH_MOUSE_BUTTON_FORWARD: i32 = 3;
const OH_MOUSE_BUTTON_BACK: i32 = 4;

// Input_TouchEventAction
const TOUCH_ACTION_DOWN: i32 = 1;
const TOUCH_ACTION_MOVE: i32 = 2;
const TOUCH_ACTION_UP: i32 = 3;

// HarmonyOS oh_key_code.h
const KEYCODE_HOME: i32 = 1;
const KEYCODE_BACK: i32 = 2;
const KEYCODE_VOLUME_UP: i32 = 16;
const KEYCODE_VOLUME_DOWN: i32 = 17;
const KEYCODE_POWER: i32 = 18;
const KEYCODE_VOLUME_MUTE: i32 = 22;
const KEYCODE_0: i32 = 2000;
const KEYCODE_DPAD_UP: i32 = 2012;
const KEYCODE_DPAD_DOWN: i32 = 2013;
const KEYCODE_DPAD_LEFT: i32 = 2014;
const KEYCODE_DPAD_RIGHT: i32 = 2015;
const KEYCODE_A: i32 = 2017;
const KEYCODE_COMMA: i32 = 2043;
const KEYCODE_PERIOD: i32 = 2044;
const KEYCODE_ALT_LEFT: i32 = 2045;
const KEYCODE_ALT_RIGHT: i32 = 2046;
const KEYCODE_SHIFT_LEFT: i32 = 2047;
const KEYCODE_SHIFT_RIGHT: i32 = 2048;
const KEYCODE_TAB: i32 = 2049;
const KEYCODE_SPACE: i32 = 2050;
const KEYCODE_ENTER: i32 = 2054;
const KEYCODE_DEL: i32 = 2055; // Backspace
const KEYCODE_GRAVE: i32 = 2056;
const KEYCODE_MINUS: i32 = 2057;
const KEYCODE_EQUALS: i32 = 2058;
const KEYCODE_LEFT_BRACKET: i32 = 2059;
const KEYCODE_RIGHT_BRACKET: i32 = 2060;
const KEYCODE_BACKSLASH: i32 = 2061;
const KEYCODE_SEMICOLON: i32 = 2062;
const KEYCODE_APOSTROPHE: i32 = 2063;
const KEYCODE_SLASH: i32 = 2064;
const KEYCODE_PAGE_UP: i32 = 2068;
const KEYCODE_PAGE_DOWN: i32 = 2069;
const KEYCODE_ESCAPE: i32 = 2070;
const KEYCODE_FORWARD_DEL: i32 = 2071; // Delete
const KEYCODE_CTRL_LEFT: i32 = 2072;
const KEYCODE_CTRL_RIGHT: i32 = 2073;
const KEYCODE_CAPS_LOCK: i32 = 2074;
const KEYCODE_SCROLL_LOCK: i32 = 2075;
const KEYCODE_META_LEFT: i32 = 2076;
const KEYCODE_META_RIGHT: i32 = 2077;
const KEYCODE_SYSRQ: i32 = 2079;
const KEYCODE_BREAK: i32 = 2080;
const KEYCODE_MOVE_HOME: i32 = 2081;
const KEYCODE_MOVE_END: i32 = 2082;
const KEYCODE_INSERT: i32 = 2083;
const KEYCODE_F1: i32 = 2090;
const KEYCODE_NUM_LOCK: i32 = 2102;
const KEYCODE_NUMPAD_0: i32 = 2103;
const KEYCODE_NUMPAD_DIVIDE: i32 = 2113;
const KEYCODE_NUMPAD_MULTIPLY: i32 = 2114;
const KEYCODE_NUMPAD_SUBTRACT: i32 = 2115;
const KEYCODE_NUMPAD_ADD: i32 = 2116;
const KEYCODE_NUMPAD_DOT: i32 = 2117;
const KEYCODE_NUMPAD_COMMA: i32 = 2118;
const KEYCODE_NUMPAD_ENTER: i32 = 2119;
const KEYCODE_NUMPAD_EQUALS: i32 = 2120;

#[repr(C)]
struct Timespec {
    tv_sec: i64,
    tv_nsec: i64,
}

extern "C" {
    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn clock_gettime(clk_id: c_int, tp: *mut Timespec) -> c_int;
}

type MouseMonitorCb = unsafe extern "C" fn(*const c_void);

struct OhInputApi {
    create_mouse: unsafe extern "C" fn() -> *mut c_void,
    destroy_mouse: unsafe extern "C" fn(*mut *mut c_void),
    set_mouse_action: unsafe extern "C" fn(*mut c_void, i32),
    set_mouse_x: unsafe extern "C" fn(*mut c_void, i32),
    set_mouse_y: unsafe extern "C" fn(*mut c_void, i32),
    set_mouse_button: unsafe extern "C" fn(*mut c_void, i32),
    set_mouse_axis_type: unsafe extern "C" fn(*mut c_void, i32),
    set_mouse_axis_value: unsafe extern "C" fn(*mut c_void, f32),
    set_mouse_action_time: Option<unsafe extern "C" fn(*mut c_void, i64)>,
    inject_mouse: unsafe extern "C" fn(*const c_void) -> i32,
    get_mouse_x: unsafe extern "C" fn(*const c_void) -> i32,
    get_mouse_y: unsafe extern "C" fn(*const c_void) -> i32,

    create_key: unsafe extern "C" fn() -> *mut c_void,
    destroy_key: unsafe extern "C" fn(*mut *mut c_void),
    set_key_action: unsafe extern "C" fn(*mut c_void, i32),
    set_key_code: unsafe extern "C" fn(*mut c_void, i32),
    set_key_action_time: Option<unsafe extern "C" fn(*mut c_void, i64)>,
    inject_key: unsafe extern "C" fn(*const c_void) -> i32,

    create_touch: unsafe extern "C" fn() -> *mut c_void,
    destroy_touch: unsafe extern "C" fn(*mut *mut c_void),
    set_touch_action: unsafe extern "C" fn(*mut c_void, i32),
    set_touch_finger_id: unsafe extern "C" fn(*mut c_void, i32),
    set_touch_x: unsafe extern "C" fn(*mut c_void, i32),
    set_touch_y: unsafe extern "C" fn(*mut c_void, i32),
    set_touch_action_time: Option<unsafe extern "C" fn(*mut c_void, i64)>,
    inject_touch: unsafe extern "C" fn(*const c_void) -> i32,

    query_authorized_status: Option<unsafe extern "C" fn(*mut i32) -> i32>,
    add_mouse_monitor: Option<unsafe extern "C" fn(MouseMonitorCb) -> i32>,
    remove_mouse_monitor: Option<unsafe extern "C" fn(MouseMonitorCb) -> i32>,
    get_pointer_location: Option<unsafe extern "C" fn(*mut i32, *mut f64, *mut f64) -> i32>,
}

unsafe impl Send for OhInputApi {}
unsafe impl Sync for OhInputApi {}

static OH_INPUT_API: OnceLock<Option<OhInputApi>> = OnceLock::new();
static CONTROL_DEVICE_GRANTED: AtomicBool = AtomicBool::new(false);
static MONITOR_ACTIVE: AtomicBool = AtomicBool::new(false);
static MONITOR_DENIED: AtomicBool = AtomicBool::new(false);

static LAST_CURSOR_X: AtomicI32 = AtomicI32::new(INVALID_CURSOR_POS);
static LAST_CURSOR_Y: AtomicI32 = AtomicI32::new(INVALID_CURSOR_POS);

lazy_static::lazy_static! {
    static ref LAST_PEER_INPUT: Mutex<(i32, i64)> = Mutex::new((0, 0));
    static ref PRESSED_MODIFIERS: Mutex<HashSet<i32>> = Mutex::new(HashSet::new());
}

unsafe fn load_sym<T>(handle: *mut c_void, name: &[u8]) -> Option<T> {
    let ptr = dlsym(handle, name.as_ptr() as *const c_char);
    if ptr.is_null() {
        None
    } else {
        Some(std::mem::transmute_copy(&ptr))
    }
}

fn api() -> Option<&'static OhInputApi> {
    OH_INPUT_API
        .get_or_init(|| unsafe {
            let handle = dlopen(b"libohinput.so\0".as_ptr() as *const c_char, RTLD_NOW | RTLD_LOCAL);
            if handle.is_null() {
                log::warn!("ohos_input: failed to dlopen libohinput.so");
                return None;
            }
            Some(OhInputApi {
                create_mouse: load_sym(handle, b"OH_Input_CreateMouseEvent\0")?,
                destroy_mouse: load_sym(handle, b"OH_Input_DestroyMouseEvent\0")?,
                set_mouse_action: load_sym(handle, b"OH_Input_SetMouseEventAction\0")?,
                set_mouse_x: load_sym(handle, b"OH_Input_SetMouseEventDisplayX\0")?,
                set_mouse_y: load_sym(handle, b"OH_Input_SetMouseEventDisplayY\0")?,
                set_mouse_button: load_sym(handle, b"OH_Input_SetMouseEventButton\0")?,
                set_mouse_axis_type: load_sym(handle, b"OH_Input_SetMouseEventAxisType\0")?,
                set_mouse_axis_value: load_sym(handle, b"OH_Input_SetMouseEventAxisValue\0")?,
                set_mouse_action_time: load_sym(handle, b"OH_Input_SetMouseEventActionTime\0"),
                inject_mouse: load_sym(handle, b"OH_Input_InjectMouseEvent\0")?,
                get_mouse_x: load_sym(handle, b"OH_Input_GetMouseEventDisplayX\0")?,
                get_mouse_y: load_sym(handle, b"OH_Input_GetMouseEventDisplayY\0")?,

                create_key: load_sym(handle, b"OH_Input_CreateKeyEvent\0")?,
                destroy_key: load_sym(handle, b"OH_Input_DestroyKeyEvent\0")?,
                set_key_action: load_sym(handle, b"OH_Input_SetKeyEventAction\0")?,
                set_key_code: load_sym(handle, b"OH_Input_SetKeyEventKeyCode\0")?,
                set_key_action_time: load_sym(handle, b"OH_Input_SetKeyEventActionTime\0"),
                inject_key: load_sym(handle, b"OH_Input_InjectKeyEvent\0")?,

                create_touch: load_sym(handle, b"OH_Input_CreateTouchEvent\0")?,
                destroy_touch: load_sym(handle, b"OH_Input_DestroyTouchEvent\0")?,
                set_touch_action: load_sym(handle, b"OH_Input_SetTouchEventAction\0")?,
                set_touch_finger_id: load_sym(handle, b"OH_Input_SetTouchEventFingerId\0")?,
                set_touch_x: load_sym(handle, b"OH_Input_SetTouchEventDisplayX\0")?,
                set_touch_y: load_sym(handle, b"OH_Input_SetTouchEventDisplayY\0")?,
                set_touch_action_time: load_sym(handle, b"OH_Input_SetTouchEventActionTime\0"),
                inject_touch: load_sym(handle, b"OH_Input_InjectTouchEvent\0")?,

                query_authorized_status: load_sym(handle, b"OH_Input_QueryAuthorizedStatus\0"),
                add_mouse_monitor: load_sym(handle, b"OH_Input_AddMouseEventMonitor\0"),
                remove_mouse_monitor: load_sym(handle, b"OH_Input_RemoveMouseEventMonitor\0"),
                get_pointer_location: load_sym(handle, b"OH_Input_GetPointerLocation\0"),
            })
        })
        .as_ref()
}

fn monotonic_us() -> i64 {
    let mut ts = Timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe {
        if clock_gettime(CLOCK_MONOTONIC, &mut ts) == 0 {
            return ts.tv_sec.saturating_mul(1_000_000).saturating_add(ts.tv_nsec / 1_000);
        }
    }
    0
}

pub fn set_control_device_granted(granted: bool) {
    let prev = CONTROL_DEVICE_GRANTED.swap(granted, Ordering::SeqCst);
    if prev != granted {
        log::info!("ohos_input: CONTROL_DEVICE granted={granted}");
    }
}

pub fn is_ohos_input_authorized() -> bool {
    let Some(api) = api() else {
        return false;
    };
    if CONTROL_DEVICE_GRANTED.load(Ordering::Acquire) {
        return true;
    }
    if let Some(query) = api.query_authorized_status {
        let mut status: i32 = INJECTION_UNAUTHORIZED;
        let ret = unsafe { query(&mut status) };
        if ret == 0 && status == INJECTION_AUTHORIZED {
            return true;
        }
    }
    false
}

pub fn host_input_status() -> i32 {
    if is_ohos_input_authorized() {
        INJECTION_AUTHORIZED
    } else {
        INJECTION_UNAUTHORIZED
    }
}

unsafe extern "C" fn on_mouse_event_monitor(event: *const c_void) {
    if event.is_null() {
        return;
    }
    if let Some(api) = api() {
        let x = (api.get_mouse_x)(event);
        let y = (api.get_mouse_y)(event);
        if x >= 0 && y >= 0 {
            LAST_CURSOR_X.store(x, Ordering::Relaxed);
            LAST_CURSOR_Y.store(y, Ordering::Relaxed);
        }
    }
}

pub fn start_cursor_monitor() {
    if MONITOR_ACTIVE.load(Ordering::Acquire) || MONITOR_DENIED.load(Ordering::Acquire) {
        return;
    }
    let Some(api) = api() else {
        return;
    };
    let Some(add_monitor) = api.add_mouse_monitor else {
        return;
    };
    let ret = unsafe { add_monitor(on_mouse_event_monitor) };
    if ret == 0 {
        MONITOR_ACTIVE.store(true, Ordering::Release);
        log::info!("ohos_input: mouse event monitor registered (INPUT_MONITORING)");
    } else {
        // 201 = INPUT_PERMISSION_DENIED; stop retrying for this capture run.
        if ret == 201 {
            MONITOR_DENIED.store(true, Ordering::Release);
        }
        log::info!("ohos_input: OH_Input_AddMouseEventMonitor returned {ret}");
    }
}

pub fn stop_cursor_monitor() {
    MONITOR_DENIED.store(false, Ordering::Release);
    if !MONITOR_ACTIVE.swap(false, Ordering::AcqRel) {
        return;
    }
    if let Some(api) = api() {
        if let Some(remove_monitor) = api.remove_mouse_monitor {
            let ret = unsafe { remove_monitor(on_mouse_event_monitor) };
            log::info!("ohos_input: mouse event monitor removed (ret={ret})");
        }
    }
}

struct StatePos {
    cursor_pos: (i32, i32),
}

impl Default for StatePos {
    fn default() -> Self {
        Self {
            cursor_pos: (INVALID_CURSOR_POS, INVALID_CURSOR_POS),
        }
    }
}

impl super::service::Reset for StatePos {
    fn reset(&mut self) {
        self.cursor_pos = (INVALID_CURSOR_POS, INVALID_CURSOR_POS);
    }
}

pub fn new_pos() -> GenericService {
    let svc = EmptyExtraFieldService::new(NAME_POS.to_owned(), false);
    GenericService::repeat::<StatePos, _, _>(&svc.clone(), 33, run_pos);
    svc.sp
}

fn current_cursor_pos() -> Option<(i32, i32)> {
    if scrap::ohos::screencap::is_running() && !MONITOR_ACTIVE.load(Ordering::Acquire) {
        start_cursor_monitor();
    }
    let x = LAST_CURSOR_X.load(Ordering::Relaxed);
    let y = LAST_CURSOR_Y.load(Ordering::Relaxed);
    if x != INVALID_CURSOR_POS && y != INVALID_CURSOR_POS {
        return Some((x, y));
    }
    if let Some(api) = api() {
        if let Some(get_loc) = api.get_pointer_location {
            let mut display_id: i32 = 0;
            let mut dx: f64 = 0.0;
            let mut dy: f64 = 0.0;
            if unsafe { get_loc(&mut display_id, &mut dx, &mut dy) } == 0 {
                let ix = dx.round() as i32;
                let iy = dy.round() as i32;
                LAST_CURSOR_X.store(ix, Ordering::Relaxed);
                LAST_CURSOR_Y.store(iy, Ordering::Relaxed);
                return Some((ix, iy));
            }
        }
    }
    None
}

fn run_pos(sp: EmptyExtraFieldService, state: &mut StatePos) -> ResultType<()> {
    let Some((x, y)) = current_cursor_pos() else {
        return Ok(());
    };
    if state.cursor_pos.0 != INVALID_CURSOR_POS && state.cursor_pos != (x, y) {
        let mut msg_out = Message::new();
        msg_out.set_cursor_position(CursorPosition {
            x,
            y,
            ..Default::default()
        });
        let exclude = {
            let now = get_time();
            let (conn, ts) = *LAST_PEER_INPUT.lock().unwrap();
            if now - ts < 300 {
                conn
            } else {
                0
            }
        };
        sp.send_without(msg_out, exclude);
    }
    state.cursor_pos = (x, y);

    sp.snapshot(|sps| {
        let mut msg_out = Message::new();
        msg_out.set_cursor_position(CursorPosition {
            x: state.cursor_pos.0,
            y: state.cursor_pos.1,
            ..Default::default()
        });
        sps.send(msg_out);
        Ok(())
    })?;
    Ok(())
}

fn inject_mouse_raw(
    api: &OhInputApi,
    action: i32,
    x: i32,
    y: i32,
    button: i32,
    axis: Option<(i32, f32)>,
) {
    unsafe {
        let mut ev = (api.create_mouse)();
        if ev.is_null() {
            return;
        }
        (api.set_mouse_action)(ev, action);
        (api.set_mouse_x)(ev, x);
        (api.set_mouse_y)(ev, y);
        (api.set_mouse_button)(ev, button);
        if let Some((axis_type, value)) = axis {
            (api.set_mouse_axis_type)(ev, axis_type);
            (api.set_mouse_axis_value)(ev, value);
        }
        if let Some(set_time) = api.set_mouse_action_time {
            set_time(ev, monotonic_us());
        }
        let ret = (api.inject_mouse)(ev);
        if ret != 0 {
            hbb_common::throttled_log!(
                std::time::Duration::from_secs(5),
                warn,
                "ohos_input: OH_Input_InjectMouseEvent(action={action}) failed: {ret}"
            );
        }
        (api.destroy_mouse)(&mut ev);
    }
}

fn inject_key_raw(api: &OhInputApi, keycode: i32, down: bool) {
    if keycode < 0 {
        return;
    }
    let is_mod = is_modifier_keycode(keycode);
    if is_mod {
        let mut pressed = PRESSED_MODIFIERS.lock().unwrap();
        if down {
            pressed.insert(keycode);
        } else {
            pressed.remove(&keycode);
        }
    }
    unsafe {
        let mut ev = (api.create_key)();
        if ev.is_null() {
            return;
        }
        (api.set_key_action)(ev, if down { KEY_ACTION_DOWN } else { KEY_ACTION_UP });
        (api.set_key_code)(ev, keycode);
        if let Some(set_time) = api.set_key_action_time {
            set_time(ev, monotonic_us());
        }
        let ret = (api.inject_key)(ev);
        if ret != 0 {
            hbb_common::throttled_log!(
                std::time::Duration::from_secs(5),
                warn,
                "ohos_input: OH_Input_InjectKeyEvent(code={keycode}, down={down}) failed: {ret}"
            );
        }
        (api.destroy_key)(&mut ev);
    }
}

fn inject_touch_raw(api: &OhInputApi, action: i32, finger_id: i32, x: i32, y: i32) {
    unsafe {
        let mut ev = (api.create_touch)();
        if ev.is_null() {
            return;
        }
        (api.set_touch_action)(ev, action);
        (api.set_touch_finger_id)(ev, finger_id);
        (api.set_touch_x)(ev, x);
        (api.set_touch_y)(ev, y);
        if let Some(set_time) = api.set_touch_action_time {
            set_time(ev, monotonic_us());
        }
        let ret = (api.inject_touch)(ev);
        if ret != 0 {
            hbb_common::throttled_log!(
                std::time::Duration::from_secs(5),
                warn,
                "ohos_input: OH_Input_InjectTouchEvent(action={action}) failed: {ret}"
            );
        }
        (api.destroy_touch)(&mut ev);
    }
}

fn is_modifier_keycode(code: i32) -> bool {
    matches!(
        code,
        KEYCODE_SHIFT_LEFT
            | KEYCODE_SHIFT_RIGHT
            | KEYCODE_CTRL_LEFT
            | KEYCODE_CTRL_RIGHT
            | KEYCODE_ALT_LEFT
            | KEYCODE_ALT_RIGHT
            | KEYCODE_META_LEFT
            | KEYCODE_META_RIGHT
    )
}

pub fn release_all_modifiers() {
    let Some(api) = api() else {
        return;
    };
    let to_release: Vec<i32> = {
        let mut pressed = PRESSED_MODIFIERS.lock().unwrap();
        pressed.drain().collect()
    };
    for code in to_release {
        inject_key_raw(api, code, false);
    }
}

fn clamp_to_screen(x: i32, y: i32) -> (i32, i32) {
    let (w, h) = scrap::ohos::screencap::screen_size();
    let cx = if w > 0 { x.clamp(0, w - 1) } else { x.max(0) };
    let cy = if h > 0 { y.clamp(0, h - 1) } else { y.max(0) };
    (cx, cy)
}

fn map_mouse_button(buttons: i32) -> i32 {
    match buttons {
        MOUSE_BUTTON_LEFT => OH_MOUSE_BUTTON_LEFT,
        MOUSE_BUTTON_RIGHT => OH_MOUSE_BUTTON_RIGHT,
        MOUSE_BUTTON_WHEEL => OH_MOUSE_BUTTON_MIDDLE,
        MOUSE_BUTTON_BACK => OH_MOUSE_BUTTON_BACK,
        MOUSE_BUTTON_FORWARD => OH_MOUSE_BUTTON_FORWARD,
        _ => OH_MOUSE_BUTTON_NONE,
    }
}

pub fn handle_mouse(evt: &MouseEvent, conn: i32) {
    if !is_ohos_input_authorized() {
        return;
    }
    let Some(api) = api() else {
        return;
    };
    let evt_type = evt.mask & MOUSE_TYPE_MASK;
    let buttons = evt.mask >> 3;

    let mut temp_mods = Vec::new();
    if evt_type == MOUSE_TYPE_DOWN {
        for m in &evt.modifiers {
            if let Ok(ck) = m.enum_value() {
                if let Some(code) = control_key_to_ohos(ck) {
                    if is_modifier_keycode(code)
                        && !PRESSED_MODIFIERS.lock().unwrap().contains(&code)
                    {
                        inject_key_raw(api, code, true);
                        temp_mods.push(code);
                    }
                }
            }
        }
    }

    match evt_type {
        MOUSE_TYPE_MOVE => {
            let (x, y) = clamp_to_screen(evt.x, evt.y);
            LAST_CURSOR_X.store(x, Ordering::Relaxed);
            LAST_CURSOR_Y.store(y, Ordering::Relaxed);
            *LAST_PEER_INPUT.lock().unwrap() = (conn, get_time());
            inject_mouse_raw(api, MOUSE_ACTION_MOVE, x, y, OH_MOUSE_BUTTON_NONE, None);
        }
        MOUSE_TYPE_MOVE_RELATIVE => {
            const MAX_DELTA: i32 = 10000;
            let dx = evt.x.clamp(-MAX_DELTA, MAX_DELTA);
            let dy = evt.y.clamp(-MAX_DELTA, MAX_DELTA);
            let cur_x = LAST_CURSOR_X.load(Ordering::Relaxed);
            let cur_y = LAST_CURSOR_Y.load(Ordering::Relaxed);
            let base_x = if cur_x == INVALID_CURSOR_POS { 0 } else { cur_x };
            let base_y = if cur_y == INVALID_CURSOR_POS { 0 } else { cur_y };
            let (x, y) = clamp_to_screen(base_x.saturating_add(dx), base_y.saturating_add(dy));
            LAST_CURSOR_X.store(x, Ordering::Relaxed);
            LAST_CURSOR_Y.store(y, Ordering::Relaxed);
            *LAST_PEER_INPUT.lock().unwrap() = (conn, get_time());
            inject_mouse_raw(api, MOUSE_ACTION_MOVE, x, y, OH_MOUSE_BUTTON_NONE, None);
        }
        MOUSE_TYPE_DOWN | MOUSE_TYPE_UP => {
            let btn = map_mouse_button(buttons);
            if btn != OH_MOUSE_BUTTON_NONE {
                let cx = LAST_CURSOR_X.load(Ordering::Relaxed);
                let cy = LAST_CURSOR_Y.load(Ordering::Relaxed);
                let (x, y) = if cx != INVALID_CURSOR_POS && cy != INVALID_CURSOR_POS {
                    (cx, cy)
                } else {
                    clamp_to_screen(evt.x, evt.y)
                };
                let action = if evt_type == MOUSE_TYPE_DOWN {
                    MOUSE_ACTION_BUTTON_DOWN
                } else {
                    MOUSE_ACTION_BUTTON_UP
                };
                inject_mouse_raw(api, action, x, y, btn, None);
            }
        }
        MOUSE_TYPE_WHEEL | MOUSE_TYPE_TRACKPAD => {
            let cx = LAST_CURSOR_X.load(Ordering::Relaxed);
            let cy = LAST_CURSOR_Y.load(Ordering::Relaxed);
            let x = if cx == INVALID_CURSOR_POS { 0 } else { cx };
            let y = if cy == INVALID_CURSOR_POS { 0 } else { cy };
            if evt.y != 0 {
                // Positive evt.y means scroll up in RustDesk; OHOS positive axis scrolls down.
                let val = -(evt.y as f32) * 15.0;
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_BEGIN,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_VERTICAL, 0.0)),
                );
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_UPDATE,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_VERTICAL, val)),
                );
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_END,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_VERTICAL, 0.0)),
                );
            }
            if evt.x != 0 {
                let val = -(evt.x as f32) * 15.0;
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_BEGIN,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_HORIZONTAL, 0.0)),
                );
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_UPDATE,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_HORIZONTAL, val)),
                );
                inject_mouse_raw(
                    api,
                    MOUSE_ACTION_AXIS_END,
                    x,
                    y,
                    OH_MOUSE_BUTTON_NONE,
                    Some((MOUSE_AXIS_SCROLL_HORIZONTAL, 0.0)),
                );
            }
        }
        _ => {}
    }

    for code in temp_mods.into_iter().rev() {
        inject_key_raw(api, code, false);
    }
}

pub fn handle_pointer(evt: &PointerDeviceEvent, _conn: i32) {
    if !is_ohos_input_authorized() {
        return;
    }
    let Some(api) = api() else {
        return;
    };
    if let Some(pointer_device_event::Union::TouchEvent(touch)) = &evt.union {
        match &touch.union {
            Some(touch_event::Union::PanStart(p)) => {
                let (x, y) = clamp_to_screen(p.x, p.y);
                inject_touch_raw(api, TOUCH_ACTION_DOWN, 0, x, y);
            }
            Some(touch_event::Union::PanUpdate(p)) => {
                let (x, y) = clamp_to_screen(p.x, p.y);
                inject_touch_raw(api, TOUCH_ACTION_MOVE, 0, x, y);
            }
            Some(touch_event::Union::PanEnd(p)) => {
                let (x, y) = clamp_to_screen(p.x, p.y);
                inject_touch_raw(api, TOUCH_ACTION_UP, 0, x, y);
            }
            _ => {}
        }
    }
}

pub fn handle_key(evt: &KeyEvent) {
    if !is_ohos_input_authorized() {
        return;
    }
    let Some(api) = api() else {
        return;
    };

    let mut temp_mods = Vec::new();
    if evt.down || evt.press {
        for m in &evt.modifiers {
            if let Ok(ck) = m.enum_value() {
                if let Some(code) = control_key_to_ohos(ck) {
                    if is_modifier_keycode(code)
                        && !PRESSED_MODIFIERS.lock().unwrap().contains(&code)
                    {
                        inject_key_raw(api, code, true);
                        temp_mods.push(code);
                    }
                }
            }
        }
    }

    match &evt.union {
        Some(key_event::Union::ControlKey(ck)) => {
            if let Ok(key) = ck.enum_value() {
                if let Some(code) = control_key_to_ohos(key) {
                    emit_key(api, code, false, evt.down, evt.press);
                }
            }
        }
        Some(key_event::Union::Chr(code)) => {
            if evt.mode.enum_value() == Ok(KeyboardMode::Map) {
                let rdev_key = crate::keyboard::keycode_to_rdev_key(*code);
                if let Some(oh_code) = rdev_key_to_ohos(rdev_key) {
                    emit_key(api, oh_code, false, evt.down, evt.press);
                }
            } else if let Some(ch) = char::from_u32(*code & 0xFFFF) {
                if let Some((oh_code, shift)) = ascii_char_to_ohos(ch) {
                    emit_key(api, oh_code, shift, evt.down, evt.press);
                }
            }
        }
        Some(key_event::Union::Unicode(code)) => {
            if let Some(ch) = char::from_u32(*code) {
                if let Some((oh_code, shift)) = ascii_char_to_ohos(ch) {
                    emit_key(api, oh_code, shift, true, true);
                }
            }
        }
        Some(key_event::Union::Seq(seq)) => {
            for ch in seq.chars() {
                if let Some((oh_code, shift)) = ascii_char_to_ohos(ch) {
                    emit_key(api, oh_code, shift, true, true);
                }
            }
        }
        _ => {}
    }

    for code in temp_mods.into_iter().rev() {
        inject_key_raw(api, code, false);
    }
}

fn emit_key(api: &OhInputApi, code: i32, shift: bool, down: bool, press: bool) {
    let need_shift = shift
        && (down || press)
        && !PRESSED_MODIFIERS.lock().unwrap().contains(&KEYCODE_SHIFT_LEFT)
        && !PRESSED_MODIFIERS.lock().unwrap().contains(&KEYCODE_SHIFT_RIGHT);
    if need_shift {
        inject_key_raw(api, KEYCODE_SHIFT_LEFT, true);
    }
    if press {
        inject_key_raw(api, code, true);
        inject_key_raw(api, code, false);
    } else {
        inject_key_raw(api, code, down);
    }
    if need_shift {
        inject_key_raw(api, KEYCODE_SHIFT_LEFT, false);
    }
}

fn control_key_to_ohos(ck: ControlKey) -> Option<i32> {
    Some(match ck {
        ControlKey::Alt | ControlKey::Option | ControlKey::Menu => KEYCODE_ALT_LEFT,
        ControlKey::RAlt => KEYCODE_ALT_RIGHT,
        ControlKey::Shift => KEYCODE_SHIFT_LEFT,
        ControlKey::RShift => KEYCODE_SHIFT_RIGHT,
        ControlKey::Control => KEYCODE_CTRL_LEFT,
        ControlKey::RControl => KEYCODE_CTRL_RIGHT,
        ControlKey::Meta => KEYCODE_META_LEFT,
        ControlKey::RWin => KEYCODE_META_RIGHT,
        ControlKey::Backspace => KEYCODE_DEL,
        ControlKey::Delete => KEYCODE_FORWARD_DEL,
        ControlKey::Return => KEYCODE_ENTER,
        ControlKey::Tab => KEYCODE_TAB,
        ControlKey::Space => KEYCODE_SPACE,
        ControlKey::Escape => KEYCODE_ESCAPE,
        ControlKey::CapsLock => KEYCODE_CAPS_LOCK,
        ControlKey::NumLock => KEYCODE_NUM_LOCK,
        ControlKey::Scroll => KEYCODE_SCROLL_LOCK,
        ControlKey::UpArrow => KEYCODE_DPAD_UP,
        ControlKey::DownArrow => KEYCODE_DPAD_DOWN,
        ControlKey::LeftArrow => KEYCODE_DPAD_LEFT,
        ControlKey::RightArrow => KEYCODE_DPAD_RIGHT,
        ControlKey::Home => KEYCODE_MOVE_HOME,
        ControlKey::End => KEYCODE_MOVE_END,
        ControlKey::PageUp => KEYCODE_PAGE_UP,
        ControlKey::PageDown => KEYCODE_PAGE_DOWN,
        ControlKey::Insert => KEYCODE_INSERT,
        ControlKey::Snapshot | ControlKey::Print => KEYCODE_SYSRQ,
        ControlKey::Pause => KEYCODE_BREAK,
        ControlKey::F1 => KEYCODE_F1,
        ControlKey::F2 => KEYCODE_F1 + 1,
        ControlKey::F3 => KEYCODE_F1 + 2,
        ControlKey::F4 => KEYCODE_F1 + 3,
        ControlKey::F5 => KEYCODE_F1 + 4,
        ControlKey::F6 => KEYCODE_F1 + 5,
        ControlKey::F7 => KEYCODE_F1 + 6,
        ControlKey::F8 => KEYCODE_F1 + 7,
        ControlKey::F9 => KEYCODE_F1 + 8,
        ControlKey::F10 => KEYCODE_F1 + 9,
        ControlKey::F11 => KEYCODE_F1 + 10,
        ControlKey::F12 => KEYCODE_F1 + 11,
        ControlKey::Numpad0 => KEYCODE_NUMPAD_0,
        ControlKey::Numpad1 => KEYCODE_NUMPAD_0 + 1,
        ControlKey::Numpad2 => KEYCODE_NUMPAD_0 + 2,
        ControlKey::Numpad3 => KEYCODE_NUMPAD_0 + 3,
        ControlKey::Numpad4 => KEYCODE_NUMPAD_0 + 4,
        ControlKey::Numpad5 => KEYCODE_NUMPAD_0 + 5,
        ControlKey::Numpad6 => KEYCODE_NUMPAD_0 + 6,
        ControlKey::Numpad7 => KEYCODE_NUMPAD_0 + 7,
        ControlKey::Numpad8 => KEYCODE_NUMPAD_0 + 8,
        ControlKey::Numpad9 => KEYCODE_NUMPAD_0 + 9,
        ControlKey::Divide => KEYCODE_NUMPAD_DIVIDE,
        ControlKey::Multiply => KEYCODE_NUMPAD_MULTIPLY,
        ControlKey::Subtract => KEYCODE_NUMPAD_SUBTRACT,
        ControlKey::Add => KEYCODE_NUMPAD_ADD,
        ControlKey::Decimal => KEYCODE_NUMPAD_DOT,
        ControlKey::Separator => KEYCODE_NUMPAD_COMMA,
        ControlKey::NumpadEnter => KEYCODE_NUMPAD_ENTER,
        ControlKey::Equals => KEYCODE_NUMPAD_EQUALS,
        ControlKey::VolumeUp => KEYCODE_VOLUME_UP,
        ControlKey::VolumeDown => KEYCODE_VOLUME_DOWN,
        ControlKey::VolumeMute => KEYCODE_VOLUME_MUTE,
        ControlKey::Power => KEYCODE_POWER,
        ControlKey::Apps => KEYCODE_HOME,
        ControlKey::Cancel => KEYCODE_BACK,
        _ => return None,
    })
}

fn rdev_key_to_ohos(key: rdev::Key) -> Option<i32> {
    use rdev::Key;
    Some(match key {
        Key::Alt => KEYCODE_ALT_LEFT,
        Key::AltGr => KEYCODE_ALT_RIGHT,
        Key::ShiftLeft => KEYCODE_SHIFT_LEFT,
        Key::ShiftRight => KEYCODE_SHIFT_RIGHT,
        Key::ControlLeft => KEYCODE_CTRL_LEFT,
        Key::ControlRight => KEYCODE_CTRL_RIGHT,
        Key::MetaLeft => KEYCODE_META_LEFT,
        Key::MetaRight => KEYCODE_META_RIGHT,
        Key::Backspace => KEYCODE_DEL,
        Key::Delete => KEYCODE_FORWARD_DEL,
        Key::Return => KEYCODE_ENTER,
        Key::Tab => KEYCODE_TAB,
        Key::Space => KEYCODE_SPACE,
        Key::Escape => KEYCODE_ESCAPE,
        Key::CapsLock => KEYCODE_CAPS_LOCK,
        Key::NumLock => KEYCODE_NUM_LOCK,
        Key::ScrollLock => KEYCODE_SCROLL_LOCK,
        Key::UpArrow => KEYCODE_DPAD_UP,
        Key::DownArrow => KEYCODE_DPAD_DOWN,
        Key::LeftArrow => KEYCODE_DPAD_LEFT,
        Key::RightArrow => KEYCODE_DPAD_RIGHT,
        Key::Home => KEYCODE_MOVE_HOME,
        Key::End => KEYCODE_MOVE_END,
        Key::PageUp => KEYCODE_PAGE_UP,
        Key::PageDown => KEYCODE_PAGE_DOWN,
        Key::Insert => KEYCODE_INSERT,
        Key::PrintScreen | Key::Print => KEYCODE_SYSRQ,
        Key::Pause => KEYCODE_BREAK,
        Key::F1 => KEYCODE_F1,
        Key::F2 => KEYCODE_F1 + 1,
        Key::F3 => KEYCODE_F1 + 2,
        Key::F4 => KEYCODE_F1 + 3,
        Key::F5 => KEYCODE_F1 + 4,
        Key::F6 => KEYCODE_F1 + 5,
        Key::F7 => KEYCODE_F1 + 6,
        Key::F8 => KEYCODE_F1 + 7,
        Key::F9 => KEYCODE_F1 + 8,
        Key::F10 => KEYCODE_F1 + 9,
        Key::F11 => KEYCODE_F1 + 10,
        Key::F12 => KEYCODE_F1 + 11,
        Key::Num0 => KEYCODE_0,
        Key::Num1 => KEYCODE_0 + 1,
        Key::Num2 => KEYCODE_0 + 2,
        Key::Num3 => KEYCODE_0 + 3,
        Key::Num4 => KEYCODE_0 + 4,
        Key::Num5 => KEYCODE_0 + 5,
        Key::Num6 => KEYCODE_0 + 6,
        Key::Num7 => KEYCODE_0 + 7,
        Key::Num8 => KEYCODE_0 + 8,
        Key::Num9 => KEYCODE_0 + 9,
        Key::KeyA => KEYCODE_A,
        Key::KeyB => KEYCODE_A + 1,
        Key::KeyC => KEYCODE_A + 2,
        Key::KeyD => KEYCODE_A + 3,
        Key::KeyE => KEYCODE_A + 4,
        Key::KeyF => KEYCODE_A + 5,
        Key::KeyG => KEYCODE_A + 6,
        Key::KeyH => KEYCODE_A + 7,
        Key::KeyI => KEYCODE_A + 8,
        Key::KeyJ => KEYCODE_A + 9,
        Key::KeyK => KEYCODE_A + 10,
        Key::KeyL => KEYCODE_A + 11,
        Key::KeyM => KEYCODE_A + 12,
        Key::KeyN => KEYCODE_A + 13,
        Key::KeyO => KEYCODE_A + 14,
        Key::KeyP => KEYCODE_A + 15,
        Key::KeyQ => KEYCODE_A + 16,
        Key::KeyR => KEYCODE_A + 17,
        Key::KeyS => KEYCODE_A + 18,
        Key::KeyT => KEYCODE_A + 19,
        Key::KeyU => KEYCODE_A + 20,
        Key::KeyV => KEYCODE_A + 21,
        Key::KeyW => KEYCODE_A + 22,
        Key::KeyX => KEYCODE_A + 23,
        Key::KeyY => KEYCODE_A + 24,
        Key::KeyZ => KEYCODE_A + 25,
        Key::BackQuote => KEYCODE_GRAVE,
        Key::Minus => KEYCODE_MINUS,
        Key::Equal => KEYCODE_EQUALS,
        Key::LeftBracket => KEYCODE_LEFT_BRACKET,
        Key::RightBracket => KEYCODE_RIGHT_BRACKET,
        Key::BackSlash => KEYCODE_BACKSLASH,
        Key::SemiColon => KEYCODE_SEMICOLON,
        Key::Quote => KEYCODE_APOSTROPHE,
        Key::Comma => KEYCODE_COMMA,
        Key::Dot => KEYCODE_PERIOD,
        Key::Slash => KEYCODE_SLASH,
        Key::Kp0 => KEYCODE_NUMPAD_0,
        Key::Kp1 => KEYCODE_NUMPAD_0 + 1,
        Key::Kp2 => KEYCODE_NUMPAD_0 + 2,
        Key::Kp3 => KEYCODE_NUMPAD_0 + 3,
        Key::Kp4 => KEYCODE_NUMPAD_0 + 4,
        Key::Kp5 => KEYCODE_NUMPAD_0 + 5,
        Key::Kp6 => KEYCODE_NUMPAD_0 + 6,
        Key::Kp7 => KEYCODE_NUMPAD_0 + 7,
        Key::Kp8 => KEYCODE_NUMPAD_0 + 8,
        Key::Kp9 => KEYCODE_NUMPAD_0 + 9,
        Key::KpDivide => KEYCODE_NUMPAD_DIVIDE,
        Key::KpMultiply => KEYCODE_NUMPAD_MULTIPLY,
        Key::KpMinus => KEYCODE_NUMPAD_SUBTRACT,
        Key::KpPlus => KEYCODE_NUMPAD_ADD,
        Key::KpDecimal => KEYCODE_NUMPAD_DOT,
        Key::KpReturn => KEYCODE_NUMPAD_ENTER,
        _ => return None,
    })
}

fn ascii_char_to_ohos(ch: char) -> Option<(i32, bool)> {
    match ch {
        'a'..='z' => Some((KEYCODE_A + (ch as i32 - 'a' as i32), false)),
        'A'..='Z' => Some((KEYCODE_A + (ch as i32 - 'A' as i32), true)),
        '0'..='9' => Some((KEYCODE_0 + (ch as i32 - '0' as i32), false)),
        ' ' => Some((KEYCODE_SPACE, false)),
        '\n' | '\r' => Some((KEYCODE_ENTER, false)),
        '\t' => Some((KEYCODE_TAB, false)),
        '`' => Some((KEYCODE_GRAVE, false)),
        '~' => Some((KEYCODE_GRAVE, true)),
        '!' => Some((KEYCODE_0 + 1, true)),
        '@' => Some((KEYCODE_0 + 2, true)),
        '#' => Some((KEYCODE_0 + 3, true)),
        '$' => Some((KEYCODE_0 + 4, true)),
        '%' => Some((KEYCODE_0 + 5, true)),
        '^' => Some((KEYCODE_0 + 6, true)),
        '&' => Some((KEYCODE_0 + 7, true)),
        '*' => Some((KEYCODE_0 + 8, true)),
        '(' => Some((KEYCODE_0 + 9, true)),
        ')' => Some((KEYCODE_0, true)),
        '-' => Some((KEYCODE_MINUS, false)),
        '_' => Some((KEYCODE_MINUS, true)),
        '=' => Some((KEYCODE_EQUALS, false)),
        '+' => Some((KEYCODE_EQUALS, true)),
        '[' => Some((KEYCODE_LEFT_BRACKET, false)),
        '{' => Some((KEYCODE_LEFT_BRACKET, true)),
        ']' => Some((KEYCODE_RIGHT_BRACKET, false)),
        '}' => Some((KEYCODE_RIGHT_BRACKET, true)),
        '\\' => Some((KEYCODE_BACKSLASH, false)),
        '|' => Some((KEYCODE_BACKSLASH, true)),
        ';' => Some((KEYCODE_SEMICOLON, false)),
        ':' => Some((KEYCODE_SEMICOLON, true)),
        '\'' => Some((KEYCODE_APOSTROPHE, false)),
        '"' => Some((KEYCODE_APOSTROPHE, true)),
        ',' => Some((KEYCODE_COMMA, false)),
        '<' => Some((KEYCODE_COMMA, true)),
        '.' => Some((KEYCODE_PERIOD, false)),
        '>' => Some((KEYCODE_PERIOD, true)),
        '/' => Some((KEYCODE_SLASH, false)),
        '?' => Some((KEYCODE_SLASH, true)),
        _ => None,
    }
}
