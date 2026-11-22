#!/usr/bin/env python3
"""Apply the minimal Flutter/OHOS adaptation to the pinned ohos-port core.

Usage: python3 core_patch.py CORE_ROOT [--check]

The pinned upstream (wanghd114/rustdesk_harmonyos, branch ohos-port) already
builds its ArkTS controller with --no-default-features --features use_dasp.
Adding `flutter` compiles src/flutter.rs and src/flutter_ffi.rs on this target
for the first time and makes InvokeUiSession require one more method, so this
script moves the desktop-only branches off OHOS and implements the missing
trait method on the ArkTS handler. flutter_video.py connects Flutter sessions
to the author's Surface C ABI and signals output-mode changes to Dart. Its
decoder-thread completion barrier lets the texture owner await deinit before
releasing the consumer without blocking the UI thread.

hbb_common, libs/scrap, the author's decoder/conversion and QoS/FPS policy,
clipboard dispatch, C ABI signatures, Flutter FRB signatures (including
is_view_camera) and typed EventToUI variants remain upstream.

Only the files listed in PATCHES are touched. CORE_ROOT must be the checkout
root at CORE_SHA with the recorded hbb_common gitlink; every patched file's
Git blob is checked against SOURCE_HASHES and the worktree must match either
that blob or this script's output, so unknown local edits are reported instead
of overwritten.

Why cfgs: OHOS reports target_os = "linux" with target_env = "ohos". Without
the redirection below, `cfg(not(any(target_os = "android", target_os = "ios")))`
branches compile on OHOS and reach crates this backend does not depend on
(X11/Wayland, GTK, DBus, PulseAudio, enigo, cpal, the desktop clipboard stack,
the texture_rgba_renderer dlopen plugin, the CM IPC daemon, core_main), and
`cfg(target_os = "linux")` branches reach platform::linux symbols the author
only compiles for the desktop.

The same target_env check keeps the author's desktop-Linux-ARM64 CJK workaround
off OHOS: cjk_ui_unavailable() in src/lang.rs forces a saved or system CJK
locale to English in translate_locale() and hides every CJK entry from
get_langs(). OHOS is also linux+aarch64 with the flutter feature, so both took
effect here and the settings list lost 简体中文 while Chinese text rendered as
English.

--check validates every input and prints each file's state without writing.
Every file is preflighted before the first write; publication is atomic per
file, not a multi-file transaction, so a rerun after an interruption accepts
the files that were already updated.

This is a source integration step, not evidence of a successful build.
"""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import flutter_video

CORE_SHA = "c48a4f109078ed27b0f9830cb85d27217988a60e"
HBB_SHA = "75ef5d8cd9b2e817047fb2eb4c2a1cdac7141dbf"
SOURCE_HASHES = {
    "src/flutter.rs": "0903ee8aa4c9743268dc9e7d86bb4567c1e82b1704028f0cd3e70f4606e613e8",
    "src/flutter_ffi.rs": "a2594fa3a08bfcc72fd4ba8a32989ef7bd884a9a40fe805c620a7ec729bd8b22",
    "src/ohos_ffi.rs": "605c3ad74e2c91500c47d10e0aca91ff161829ce62882f0ad17de11c0de6be9c",
    "src/server.rs": "53d69eed31911440e07e5f8cc3313f892b80102d0b89128b73ff75246ed8e660",
    "src/server/connection.rs": "5bd90fb62b4c4d6660c1bc1b45a0ae034b664d17e1cf9f8de11bb17bfbf669a0",
    "src/ui_interface.rs": "0d26ed8a0826e8985184e139459d7efa92dc2ec602c1ba88d3a48e173a6c92e9",
    "src/keyboard.rs": "64357790e34d85a23a6d18f5eecb05e13e89de51b599060c7f5a6f55a03707a8",
    "src/client.rs": "157c71dec9e87fea6ac64f1380c3813a04b4524d96e40d1309beb6c4237b4ac5",
    "src/ui_session_interface.rs": "63bd8f7f5b0f335cf36c924021a433bd532d077d36b02788c1f70efa47595e2a",
    "src/client/io_loop.rs": "f2285e2721b716c5948f202f5eef6a649018c81347e085775b89e73ae459c91c",
    "src/ipc.rs": "85326032ebc7514ea3da4a480f5acd79b078dc186656b80b9ae91054f84497a0",
    "src/lang.rs": "aa5be8539fc87a329653ce10d8b1e9ed79dc1e54dc7559b1eae16558fdd0568f",
    "libs/scrap/src/ohos/ohcodec.rs": "0bb6fd055f4db1ca465c29a8a31429a4c6ab02408ddc9eb33af079fd3af087b2",
    "libs/scrap/src/ohos/decoder.rs": "ee3008d57db1c7e8079ebc7154204bb996662ceaac704f562cb73fa0b71eaba1",
    "libs/scrap/src/common/codec.rs": "5752208f8d792659590f7b9f50792a1070c045d1a1327759c2f80c3e0b30676b",
    "libs/scrap/src/ohos/ffi.rs": "f9205e1834e9d687c7da59b3a25f39d1f5e1a5ab3392ba8b085f3fb53caf9458",
    "libs/scrap/src/ohos/screencap.rs": "b555671c24ac76f42e4c10f276f4a955d22d2b84aaaa60ac2595911b9938e3b5",
    "src/server/audio_service.rs": "1868a9be83c8b4b93b8c388f8374adae798b5b0d7ec6e9515019a638b68b3bc3",
}
COPY_FILES = (
    "src/server/ohos_input.rs",
)

MOBILE = 'any(target_os = "android", target_os = "ios")'
MOBILE_OHOS = 'any(target_os = "android", target_os = "ios", target_env = "ohos")'
DESKTOP_CFG = f"#[cfg(not({MOBILE}))]"
DESKTOP_OHOS_CFG = f"#[cfg(not({MOBILE_OHOS}))]"
MOBILE_CFG = f"#[cfg({MOBILE})]"
MOBILE_OHOS_CFG = f"#[cfg({MOBILE_OHOS})]"
LINUX_CFG = '#[cfg(target_os = "linux")]'
LINUX_DESKTOP_CFG = '#[cfg(all(target_os = "linux", not(target_env = "ohos")))]'
NOT_LINUX_CFG = '#[cfg(not(target_os = "linux"))]'
NOT_LINUX_DESKTOP_CFG = '#[cfg(any(not(target_os = "linux"), target_env = "ohos"))]'
ANDROID_CFG = '#[cfg(target_os = "android")]'
ANDROID_OHOS_CFG = '#[cfg(any(target_os = "android", target_env = "ohos"))]'


def replace(text, old, new, count=1):
    actual = text.count(old)
    if actual != count:
        raise ValueError(f"patch anchor count {actual}, expected {count}: {old!r}")
    return text.replace(old, new)


def cfg_lines(text, old, new, count):
    """Change only complete cfg attribute lines, not comments or cfg! values."""
    lines = text.splitlines(keepends=True)
    actual = sum(line.strip() == old for line in lines)
    if actual != count:
        raise ValueError(f"cfg count {actual}, expected {count}: {old}")
    return "".join(line.replace(old, new) if line.strip() == old else line for line in lines)


def patch_flutter(text):
    # The Flutter frontend is shared with the desktop apps. On OHOS the desktop
    # branches would reach dlopen, core_main, the desktop CM IPC relay, the
    # texture_rgba_renderer plugin, multi-display textures and the service's
    # session accounting. Take the existing mobile alternatives instead.
    text = cfg_lines(text, DESKTOP_CFG, DESKTOP_OHOS_CFG, 21)
    text = cfg_lines(text, MOBILE_CFG, MOBILE_OHOS_CFG, 7)
    # The RGBA plugin (and the .so it dlopen()s) is loaded next to the desktop
    # runner; the OHOS build uses Flutter's existing mobile RGBA path.
    text = cfg_lines(text, LINUX_CFG, LINUX_DESKTOP_CFG, 2)
    # rustdesk_core_main is the desktop CLI entry; the OHOS Ability owns the
    # process lifecycle, so its SIGPIPE setup is not applied here.
    text = replace(
        text, '#[cfg(any(target_os = "linux", target_os = "macos"))]',
        '#[cfg(all(not(target_env = "ohos"), any(target_os = "linux", target_os = "macos")))]',
    )
    # The CM UI runs in this process on OHOS (like Android): there is no IPC
    # daemon to relay connections to, so provide the in-process channel.
    for following in (
        'use hbb_common::tokio::sync::mpsc::{UnboundedReceiver, UnboundedSender};',
        'pub fn start_channel(',
    ):
        text = replace(text, ANDROID_CFG + '\n    ' + following,
                       ANDROID_OHOS_CFG + '\n    ' + following)
    # On OHOS desktop (2in1 / tablet freeform), the main isolate listens on
    # APP_TYPE_MAIN ("main") to open the SessionAbility CM window, while the CM
    # isolate listens on "cm". Broadcast CM events to both streams when present.
    text = replace(
        text,
        '            debug_assert!(h.get("name").is_none());\n'
        '            h.insert("name", json!(name));\n\n'
        '            if let Some(s) = GLOBAL_EVENT_STREAM.read().unwrap().get(super::APP_TYPE_CM) {',
        '            debug_assert!(h.get("name").is_none());\n'
        '            h.insert("name", json!(name));\n\n'
        '            #[cfg(target_env = "ohos")]\n'
        '            {\n'
        '                let payload = serde_json::ser::to_string(&h).unwrap_or("".to_owned());\n'
        '                let streams = GLOBAL_EVENT_STREAM.read().unwrap();\n'
        '                let mut sent = false;\n'
        '                for channel in [super::APP_TYPE_MAIN, "cm"] {\n'
        '                    if let Some(s) = streams.get(channel) {\n'
        '                        s.add(payload.clone());\n'
        '                        sent = true;\n'
        '                    }\n'
        '                }\n'
        '                if !sent {\n'
        '                    println!(\n'
        '                        "Push event {} failed. No {} or cm event stream found.",\n'
        '                        name,\n'
        '                        super::APP_TYPE_MAIN\n'
        '                    );\n'
        '                }\n'
        '                return;\n'
        '            }\n\n'
        '            if let Some(s) = GLOBAL_EVENT_STREAM.read().unwrap().get(super::APP_TYPE_CM) {',
    )
    return text


def patch_flutter_ffi(text):
    # Same split as flutter.rs: input grabbing, IPC, audio devices, CM, printer
    # and trusted-device management are desktop-only and must use the existing
    # mobile alternatives here.
    text = cfg_lines(text, DESKTOP_CFG, DESKTOP_OHOS_CFG, 25)
    text = cfg_lines(text, MOBILE_CFG, MOBILE_OHOS_CFG, 18)
    # X11/Wayland, SELinux, DBus and GNOME integration do not exist in this
    # backend, so keep the non-linux arm of each pair.
    text = cfg_lines(text, LINUX_CFG, LINUX_DESKTOP_CFG, 10)
    text = cfg_lines(text, NOT_LINUX_CFG, NOT_LINUX_DESKTOP_CFG, 5)
    # ui_cm_interface::close_window only exists for desktop Linux.
    text = replace(
        text, '#[cfg(all(not(target_os = "linux"), not(target_os = "ios")))]',
        '#[cfg(all(any(not(target_os = "linux"), target_env = "ohos"), not(target_os = "ios")))]',
    )
    # The wallpaper remover is a desktop platform plug-in.
    text = replace(
        text, '#[cfg(any(target_os = "windows", target_os = "linux"))]',
        '#[cfg(all(not(target_env = "ohos"), any(target_os = "windows", target_os = "linux")))]',
    )
    # Theme/language are already broadcast to the in-process Flutter isolates
    # above; send_to_cm is desktop-only here (no CM daemon to notify).
    for notification in ('Theme(dark)', 'Language(lang)'):
        text = replace(
            text, '#[cfg(not(any(target_os = "ios")))]\n'
            f'    send_to_cm(&crate::ipc::Data::{notification});',
            '#[cfg(not(any(target_os = "ios", target_env = "ohos")))]\n'
            f'    send_to_cm(&crate::ipc::Data::{notification});',
        )
    # CM options live in this process, not behind the desktop IPC daemon.
    text = replace(
        text, 'pub fn cm_get_config(name: String) -> String {\n    #[cfg(not(target_os = "ios"))]',
        'pub fn cm_get_config(name: String) -> String {\n'
        '    #[cfg(target_env = "ohos")]\n    {\n        config::Config::get_option(&name)\n    }\n'
        '    #[cfg(not(any(target_os = "ios", target_env = "ohos")))]',
    )
    # hbb_common's APP_HOME_DIR is android/ios-only. Keep this function's
    # original cfg; OHOS initialization belongs to the author's ohos_init.
    text = replace(
        text,
        'pub fn main_set_home_dir(_home: String) {\n    ' + MOBILE_OHOS_CFG + '\n'
        '    {\n        *config::APP_HOME_DIR.write().unwrap() = _home;\n    }\n}',
        'pub fn main_set_home_dir(_home: String) {\n    ' + MOBILE_CFG + '\n'
        '    {\n        *config::APP_HOME_DIR.write().unwrap() = _home;\n    }\n}',
    )
    return text


def patch_ohos_ffi(text):
    # `flutter` adds a required InvokeUiSession method. The ArkTS handler is the
    # second implementation in this build (FlutterHandler has its own): it never
    # hosts several UI sessions, matching the ArkTS single-window controller.
    text = replace(
        text, 'impl InvokeUiSession for OhosHandler {\n',
        'impl InvokeUiSession for OhosHandler {\n'
        '    #[cfg(feature = "flutter")]\n'
        '    fn is_multi_ui_session(&self) -> bool {\n        false\n    }\n\n',
    )
    text = replace(
        text,
        'fn register_capture_state_hook() {\n'
        '    scrap::ohos::screencap::set_on_state_change(Some(Box::new(|state| {\n'
        '        push_event("server_capture_state", &[("state", &state.to_string())]);\n'
        '    })));\n'
        '}\n',
        'fn register_capture_state_hook() {\n'
        '    scrap::ohos::screencap::set_on_state_change(Some(Box::new(|state| {\n'
        '        if state == 0 {\n'
        '            crate::server::input_service::start_cursor_monitor();\n'
        '        } else {\n'
        '            crate::server::input_service::stop_cursor_monitor();\n'
        '            crate::server::input_service::release_all_modifiers();\n'
        '        }\n'
        '        push_event("server_capture_state", &[("state", &state.to_string())]);\n'
        '    })));\n'
        '}\n',
    )
    text = replace(
        text,
        'pub extern "C" fn ohos_capture_stop() -> c_int {\n'
        '    scrap::ohos::screencap::stop();\n'
        '    push_event("server_capture_state", &[("state", "2")]);\n'
        '    0\n'
        '}\n',
        'pub extern "C" fn ohos_capture_stop() -> c_int {\n'
        '    crate::server::input_service::stop_cursor_monitor();\n'
        '    crate::server::input_service::release_all_modifiers();\n'
        '    scrap::ohos::screencap::stop();\n'
        '    push_event("server_capture_state", &[("state", "2")]);\n'
        '    0\n'
        '}\n',
    )
    return replace(
        text,
        '#[no_mangle]\n'
        'pub extern "C" fn ohos_capture_running() -> c_int {\n'
        '    if scrap::ohos::screencap::is_running() {\n'
        '        1\n'
        '    } else {\n'
        '        0\n'
        '    }\n'
        '}\n',
        '#[no_mangle]\n'
        'pub extern "C" fn ohos_capture_running() -> c_int {\n'
        '    if scrap::ohos::screencap::is_running() {\n'
        '        1\n'
        '    } else {\n'
        '        0\n'
        '    }\n'
        '}\n\n'
        '/// Reports whether this process can inject host input (2 = AUTHORIZED, 0 = UNAUTHORIZED).\n'
        '#[no_mangle]\n'
        'pub extern "C" fn ohos_flutter_host_input_status() -> c_int {\n'
        '    crate::server::input_service::host_input_status()\n'
        '}\n\n'
        '/// Updates the runtime `ohos.permission.CONTROL_DEVICE` grant state from ArkTS.\n'
        '#[no_mangle]\n'
        'pub extern "C" fn ohos_flutter_set_host_input_permission(granted: c_int) -> c_int {\n'
        '    crate::server::input_service::set_control_device_granted(granted != 0);\n'
        '    crate::server::input_service::host_input_status()\n'
        '}\n',
    )


def patch_server(text):
    text = replace(
        text,
        '#[cfg(any(target_os = "android", target_os = "ios", target_env = "ohos"))]\n'
        'pub mod input_service {\n'
        '    pub const NAME_CURSOR: &\'static str = "";\n'
        '    pub const NAME_POS: &\'static str = "";\n'
        '    pub const NAME_WINDOW_FOCUS: &\'static str = "";\n'
        '}\n',
        '#[cfg(any(target_os = "android", target_os = "ios"))]\n'
        'pub mod input_service {\n'
        '    pub const NAME_CURSOR: &\'static str = "";\n'
        '    pub const NAME_POS: &\'static str = "";\n'
        '    pub const NAME_WINDOW_FOCUS: &\'static str = "";\n'
        '}\n'
        '#[cfg(target_env = "ohos")]\n'
        'pub mod ohos_input;\n'
        '#[cfg(target_env = "ohos")]\n'
        'pub use ohos_input as input_service;\n',
    )
    return replace(
        text,
        '    #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '    {\n'
        '        if !display_service::capture_cursor_embedded() {\n',
        '    #[cfg(target_env = "ohos")]\n'
        '    {\n'
        '        server.add_service(Box::new(input_service::new_pos()));\n'
        '    }\n'
        '    #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '    {\n'
        '        if !display_service::capture_cursor_embedded() {\n',
    )


def patch_server_connection(text):
    # With `flutter` the CM UI is in-process, so the Flutter channel replaces the
    # ArkTS one; keeping both imports would be a duplicate `start_channel`. The
    # call site is already gated on android/ios/ohos.
    text = replace(
        text,
        MOBILE_CFG + '\nuse crate::{common::DEVICE_NAME, flutter::connection_manager::start_channel};\n'
        '#[cfg(target_env = "ohos")]\n'
        'use crate::{common::DEVICE_NAME, ohos_ffi::connection_manager::start_channel};',
        '#[cfg(any(target_os = "android", target_os = "ios", all(target_env = "ohos", feature = "flutter")))]\n'
        'use crate::{common::DEVICE_NAME, flutter::connection_manager::start_channel};\n'
        '#[cfg(all(target_env = "ohos", not(feature = "flutter")))]\n'
        'use crate::{common::DEVICE_NAME, ohos_ffi::connection_manager::start_channel};',
    )
    text = replace(
        text,
        '        #[cfg(target_env = "ohos")]\n'
        '        {\n'
        '            // Input injection needs the system-restricted INJECT_EVENT permission\n'
        '            // a third-party HarmonyOS app cannot get, so a controlled OHOS peer is\n'
        '            // view-only for now (file transfer / clipboard / audio stay available).\n'
        '            conn.keyboard = false;\n'
        '        }\n',
        '        #[cfg(target_env = "ohos")]\n'
        '        {\n'
        '            if !crate::server::input_service::is_ohos_input_authorized() {\n'
        '                conn.keyboard = false;\n'
        '            }\n'
        '        }\n',
    )
    text = replace(
        text,
        '                    #[cfg(any(target_os = "android", target_os = "ios"))]\n'
        '                    if let Err(e) = call_main_service_pointer_input("mouse", me.mask, me.x, me.y) {\n'
        '                        log::debug!("call_main_service_pointer_input fail:{}", e);\n'
        '                    }\n',
        '                    #[cfg(any(target_os = "android", target_os = "ios"))]\n'
        '                    if let Err(e) = call_main_service_pointer_input("mouse", me.mask, me.x, me.y) {\n'
        '                        log::debug!("call_main_service_pointer_input fail:{}", e);\n'
        '                    }\n'
        '                    #[cfg(target_env = "ohos")]\n'
        '                    if self.peer_keyboard_enabled() {\n'
        '                        crate::server::input_service::handle_mouse(&me, self.inner.id());\n'
        '                    }\n',
    )
    text = replace(
        text,
        '                    #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '                    if self.peer_keyboard_enabled() {\n'
        '                        MOUSE_MOVE_TIME.store(get_time(), Ordering::SeqCst);\n'
        '                        self.input_pointer(pde, self.inner.id());\n'
        '                    }\n',
        '                    #[cfg(target_env = "ohos")]\n'
        '                    if self.peer_keyboard_enabled() {\n'
        '                        crate::server::input_service::handle_pointer(&pde, self.inner.id());\n'
        '                    }\n'
        '                    #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '                    if self.peer_keyboard_enabled() {\n'
        '                        MOUSE_MOVE_TIME.store(get_time(), Ordering::SeqCst);\n'
        '                        self.input_pointer(pde, self.inner.id());\n'
        '                    }\n',
    )
    text = replace(
        text,
        '                #[cfg(any(target_os = "ios"))]\n'
        '                Some(message::Union::KeyEvent(..)) => {}\n',
        '                #[cfg(any(target_os = "ios"))]\n'
        '                Some(message::Union::KeyEvent(..)) => {}\n'
        '                #[cfg(target_env = "ohos")]\n'
        '                Some(message::Union::KeyEvent(me)) => {\n'
        '                    if !self.is_authed_view_camera_conn() && self.peer_keyboard_enabled() {\n'
        '                        crate::server::input_service::handle_key(&me);\n'
        '                    }\n'
        '                    self.update_auto_disconnect_timer();\n'
        '                }\n',
    )
    text = replace(
        text,
        '        #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '        if let Ok(q) = o.show_remote_cursor.enum_value() {\n',
        '        #[cfg(target_env = "ohos")]\n'
        '        if let Ok(q) = o.show_remote_cursor.enum_value() {\n'
        '            if q != BoolOption::NotSet {\n'
        '                self.show_remote_cursor = q == BoolOption::Yes;\n'
        '                if let Some(s) = self.server.upgrade() {\n'
        '                    s.write().unwrap().subscribe(\n'
        '                        NAME_POS,\n'
        '                        self.inner.clone(),\n'
        '                        self.show_remote_cursor,\n'
        '                    );\n'
        '                }\n'
        '            }\n'
        '        }\n'
        '        #[cfg(not(any(target_os = "android", target_os = "ios", target_env = "ohos")))]\n'
        '        if let Ok(q) = o.show_remote_cursor.enum_value() {\n',
    )
    return replace(
        text,
        '        crate::audio_service::set_voice_call_input_device(None, true);\n'
        '        log::info!("#{} Connection closed: {}", self.inner.id(), reason);\n',
        '        #[cfg(target_env = "ohos")]\n'
        '        crate::server::input_service::release_all_modifiers();\n'
        '        crate::audio_service::set_voice_call_input_device(None, true);\n'
        '        log::info!("#{} Connection closed: {}", self.inner.id(), reason);\n',
    )


def patch_ohos_screencap(text):
    text = replace(
        text,
        '    fn OH_AVScreenCapture_SetDataCallback(\n'
        '        capture: *mut OH_AVScreenCapture,\n'
        '        callback: OH_AVScreenCapture_OnBufferAvailable,\n'
        '        userData: *mut c_void,\n'
        '    ) -> c_int;\n'
        '}\n',
        '    fn OH_AVScreenCapture_SetDataCallback(\n'
        '        capture: *mut OH_AVScreenCapture,\n'
        '        callback: OH_AVScreenCapture_OnBufferAvailable,\n'
        '        userData: *mut c_void,\n'
        '    ) -> c_int;\n'
        '    fn OH_AVScreenCapture_ShowCursor(\n'
        '        capture: *mut OH_AVScreenCapture,\n'
        '        showCursor: bool,\n'
        '    ) -> c_int;\n'
        '}\n',
    )
    text = replace(
        text,
        '        config.videoInfo.videoCapInfo = OH_VideoCaptureInfo {\n'
        '            displayId: 0,\n'
        '            missionIDs: std::ptr::null_mut(),\n'
        '            missionIDsLen: 0,\n'
        '            videoFrameWidth: width,\n'
        '            videoFrameHeight: height,\n'
        '            videoSource: OH_VIDEO_SOURCE_SURFACE_RGBA,\n'
        '        };\n'
        '        let err = OH_AVScreenCapture_Init(capture, config);\n'
        '        if err != AV_SCREEN_CAPTURE_ERR_OK {\n'
        '            OH_AVScreenCapture_Release(capture);\n'
        '            return Err(format!("Init failed: {err}"));\n'
        '        }\n',
        '        config.videoInfo.videoCapInfo = OH_VideoCaptureInfo {\n'
        '            displayId: 0,\n'
        '            missionIDs: std::ptr::null_mut(),\n'
        '            missionIDsLen: 0,\n'
        '            videoFrameWidth: width,\n'
        '            videoFrameHeight: height,\n'
        '            videoSource: OH_VIDEO_SOURCE_SURFACE_RGBA,\n'
        '        };\n'
        '        config.videoInfo.videoEncInfo.videoFrameRate = 60;\n'
        '        let err = OH_AVScreenCapture_Init(capture, config);\n'
        '        if err != AV_SCREEN_CAPTURE_ERR_OK {\n'
        '            OH_AVScreenCapture_Release(capture);\n'
        '            return Err(format!("Init failed: {err}"));\n'
        '        }\n'
        '        let _ = OH_AVScreenCapture_ShowCursor(capture, true);\n'
        '        set_max_video_frame_rate(capture, 60);\n',
    )
    text = replace(
        text,
        '    let start_err = unsafe {\n'
        '        let ptr = CAPTURE.lock().unwrap().as_ref().map(|c| c.capture);\n'
        '        match ptr {\n'
        '            Some(ptr) => OH_AVScreenCapture_StartScreenCapture(ptr),\n'
        '            None => -1,\n'
        '        }\n'
        '    };\n',
        '    let start_err = unsafe {\n'
        '        let ptr = CAPTURE.lock().unwrap().as_ref().map(|c| c.capture);\n'
        '        match ptr {\n'
        '            Some(ptr) => {\n'
        '                let err = OH_AVScreenCapture_StartScreenCapture(ptr);\n'
        '                if err == AV_SCREEN_CAPTURE_ERR_OK {\n'
        '                    set_max_video_frame_rate(ptr, 60);\n'
        '                }\n'
        '                err\n'
        '            }\n'
        '            None => -1,\n'
        '        }\n'
        '    };\n',
    )
    text = replace(
        text,
        'unsafe extern "C" fn on_state_change(\n'
        '    _capture: *mut OH_AVScreenCapture,\n'
        '    state_code: c_int,\n'
        '    _userData: *mut c_void,\n'
        ') {\n'
        '    log::info!("avscreen capture state: {state_code}");\n'
        '    match state_code {\n'
        '        OH_SCREEN_CAPTURE_STATE_STARTED => notify_state(state_code),\n',
        'unsafe extern "C" fn on_state_change(\n'
        '    capture: *mut OH_AVScreenCapture,\n'
        '    state_code: c_int,\n'
        '    _userData: *mut c_void,\n'
        ') {\n'
        '    log::info!("avscreen capture state: {state_code}");\n'
        '    match state_code {\n'
        '        OH_SCREEN_CAPTURE_STATE_STARTED => {\n'
        '            if RUNNING.load(Ordering::SeqCst) {\n'
        '                set_max_video_frame_rate(capture, 60);\n'
        '            }\n'
        '            notify_state(state_code);\n'
        '        }\n',
    )
    return replace(
        text,
        'extern "C" {\n'
        '    #[link_name = "close"]\n'
        '    fn libc_close(fd: c_int) -> c_int;\n'
        '}\n',
        'const RTLD_NOW: c_int = 2;\n'
        'const RTLD_LOCAL: c_int = 0;\n'
        '\n'
        'extern "C" {\n'
        '    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;\n'
        '    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;\n'
        '    #[link_name = "close"]\n'
        '    fn libc_close(fd: c_int) -> c_int;\n'
        '}\n'
        '\n'
        'unsafe fn set_max_video_frame_rate(capture: *mut OH_AVScreenCapture, fps: i32) {\n'
        '    if capture.is_null() {\n'
        '        return;\n'
        '    }\n'
        '    type SetMaxFpsFn = unsafe extern "C" fn(*mut OH_AVScreenCapture, i32) -> c_int;\n'
        '    static SET_MAX_FPS: std::sync::OnceLock<Option<SetMaxFpsFn>> = std::sync::OnceLock::new();\n'
        '    let func = *SET_MAX_FPS.get_or_init(|| {\n'
        '        let handle = dlopen(\n'
        '            b"libnative_avscreen_capture.so\\0".as_ptr() as *const c_char,\n'
        '            RTLD_NOW | RTLD_LOCAL,\n'
        '        );\n'
        '        if handle.is_null() {\n'
        '            return None;\n'
        '        }\n'
        '        let sym = dlsym(\n'
        '            handle,\n'
        '            b"OH_AVScreenCapture_SetMaxVideoFrameRate\\0".as_ptr() as *const c_char,\n'
        '        );\n'
        '        if sym.is_null() {\n'
        '            None\n'
        '        } else {\n'
        '            Some(std::mem::transmute::<*mut c_void, SetMaxFpsFn>(sym))\n'
        '        }\n'
        '    });\n'
        '    if let Some(func) = func {\n'
        '        let ret = func(capture, fps);\n'
        '        log::info!("avscreen SetMaxVideoFrameRate({fps}) -> {ret}");\n'
        '    }\n'
        '}\n',
    )


def patch_ui_interface(text):
    # `crate::ipc` is not imported here on OHOS (no desktop daemon), so every
    # desktop-only IPC branch has to stay out of the OHOS build and the mobile
    # arm has to cover OHOS as well.
    text = replace(
        text, '#[cfg(not(any(target_os = "ios")))]\n'
        '#[tokio::main(flavor = "current_thread")]\n'
        'pub(crate) async fn send_to_cm(data: &ipc::Data)',
        '#[cfg(not(any(target_os = "ios", target_env = "ohos")))]\n'
        '#[tokio::main(flavor = "current_thread")]\n'
        'pub(crate) async fn send_to_cm(data: &ipc::Data)',
    )
    # The remote image is delivered as RGBA through Flutter's own path; the
    # texture_rgba_renderer plugin the Linux branch selects is not built here.
    text = replace(
        text, 'pub fn use_texture_render() -> bool {\n',
        'pub fn use_texture_render() -> bool {\n'
        '    #[cfg(target_env = "ohos")]\n    return false;\n',
    )
    text = replace(
        text, LINUX_CFG + '\n    return cfg!(feature = "flutter")',
        LINUX_DESKTOP_CFG + '\n    return cfg!(feature = "flutter")',
    )
    # PulseAudio source enumeration: only the desktop build queries it.
    text = replace(
        text, DESKTOP_CFG + '\npub fn get_sound_inputs()',
        DESKTOP_OHOS_CFG + '\npub fn get_sound_inputs()',
    )
    # There is no desktop proxy daemon and no desktop CM to ask for the PIN.
    text = replace(
        text, DESKTOP_CFG + '\n    return ipc::get_proxy_status();',
        DESKTOP_OHOS_CFG + '\n    return ipc::get_proxy_status();',
    )
    text = replace(
        text, '// Currently, only the desktop version has proxy settings.\n    ' + MOBILE_CFG,
        '// Currently, only the desktop version has proxy settings.\n    ' + MOBILE_OHOS_CFG,
    )
    for name in ('get_unlock_pin', 'set_unlock_pin'):
        signature = f'pub fn {name}(' + (')' if name == 'get_unlock_pin' else 'pin: String)') + ' -> String {'
        text = replace(text, signature + '\n    ' + MOBILE_CFG,
                       signature + '\n    ' + MOBILE_OHOS_CFG)
    text = replace(
        text, DESKTOP_CFG + '\n    return ipc::get_unlock_pin();',
        DESKTOP_OHOS_CFG + '\n    return ipc::get_unlock_pin();',
    )
    text = replace(
        text, DESKTOP_CFG + '\n    match ipc::set_unlock_pin(pin, true)',
        DESKTOP_OHOS_CFG + '\n    match ipc::set_unlock_pin(pin, true)',
    )
    # Desktop elevation is a platform::linux/windows/macos feature; mobile
    # platforms have nothing to elevate.
    text = replace(
        text, '#[cfg(any(windows, target_os = "linux", target_os = "macos"))]\n'
        '    return crate::platform::check_super_user_permission()',
        '#[cfg(all(not(target_env = "ohos"), any(windows, target_os = "linux", target_os = "macos")))]\n'
        '    return crate::platform::check_super_user_permission()',
    )
    return replace(
        text, '#[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]\n    return true;',
        '#[cfg(any(target_env = "ohos", not(any(windows, target_os = "linux", target_os = "macos"))))]\n    return true;',
    )


def patch_keyboard(text):
    # input_source switches between the X11 grab loop and Flutter's own grab
    # mode; both need platform::linux. Only this frontend entry point uses it,
    # and OHOS injects through the upstream Flutter path.
    return replace(
        text, DESKTOP_CFG + '\npub mod input_source {',
        DESKTOP_OHOS_CFG + '\npub mod input_source {',
    )


def patch_client(text):
    # "Switch sides" asks the local daemon for a pending uuid over desktop IPC.
    # Without that daemon OHOS takes the plain fallback and the switch does not
    # pretake the stored uuid.
    for following in (
        '\nasync fn is_switch_sides_back(',
        '\nasync fn request_local_switch_sides_uuid(',
        '\n    {\n        let uuid = lc.write().unwrap().switch_uuid.take();',
    ):
        text = replace(text, DESKTOP_CFG + following, DESKTOP_OHOS_CFG + following)
    # The fallback stub above must still be selected on flutter+OHOS.
    return replace(
        text,
        '#[cfg(not(all(feature = "flutter", not(' + MOBILE + '))))]',
        '#[cfg(not(all(feature = "flutter", not(' + MOBILE_OHOS + '))))]',
    )


def patch_ui_session_interface(text):
    # Session::switch_sides drives the desktop daemon over IPC; OHOS keeps the
    # no-op variant that mobile already uses.
    text = replace(
        text, '#[cfg(any(target_os = "android", target_os = "ios", not(feature = "flutter")))]',
        '#[cfg(any(target_os = "android", target_os = "ios", target_env = "ohos", not(feature = "flutter")))]',
    )
    return replace(
        text, DESKTOP_CFG + '\n    #[tokio::main(flavor = "current_thread")]\n    pub async fn switch_sides(',
        DESKTOP_OHOS_CFG + '\n    #[tokio::main(flavor = "current_thread")]\n    pub async fn switch_sides(',
    )


def patch_client_io_loop(text):
    # The desktop relay hands a received clipboard payload to the other local
    # sessions through flutter::send_clipboard_msg_to_other_sessions, which this
    # build does not compile. OHOS keeps the single-session dispatch below.
    text = replace(
        text, 'feature = "flutter",\n                            not(' + MOBILE + ')',
        'feature = "flutter",\n                            not(' + MOBILE_OHOS + ')', 2,
    )
    # The author excludes Client::try_start_clipboard on OHOS; its Flutter
    # caller must use the same platform boundary.
    return replace(
        text, '#[cfg(not(target_os = "ios"))]\n'
        '                            let rx = Client::try_start_clipboard(None);',
        '#[cfg(not(any(target_os = "ios", target_env = "ohos")))]\n'
        '                            let rx = Client::try_start_clipboard(None);',
    )


def patch_ipc(text):
    # ControllingSessionCount's enum variant, handler and sender must share one
    # cfg: the handler feeds crate::updater, which lib.rs excludes on OHOS.
    return replace(text, 'not(' + MOBILE + ')', 'not(' + MOBILE_OHOS + ')', 4)


def patch_lang(text):
    # The author drops the CJK UI on desktop Linux ARM64 and redirects those
    # locales to English. OHOS reports the same target_os/target_arch and builds
    # with `flutter`, so the workaround removed the CJK entries of get_langs()
    # and turned a saved or system CJK locale into English here. Only the real
    # desktop build keeps it; the translation tables and locale parsing stay
    # untouched.
    return replace(
        text,
        'pub(crate) fn cjk_ui_unavailable() -> bool {\n'
        '    cfg!(all(\n'
        '        target_os = "linux",\n'
        '        target_arch = "aarch64",\n'
        '        feature = "flutter"\n'
        '    ))\n'
        '}',
        'pub(crate) fn cjk_ui_unavailable() -> bool {\n'
        '    cfg!(all(\n'
        '        target_os = "linux",\n'
        '        not(target_env = "ohos"),\n'
        '        target_arch = "aarch64",\n'
        '        feature = "flutter"\n'
        '    ))\n'
        '}',
    )


def patch_ohcodec(text):
    # OH_MD_KEY_FRAME_RATE has value type double in HarmonyOS AVFormat; passing
    # it through OH_AVFormat_SetIntValue fails type validation ("Key's value
    # type does not match int32, key: frame_rate"), causing both H.264 and
    # H.265 hardware encoder probes and creation to fail and fall back to VP9.
    # Also raise DEFAULT_FPS to 60 and configure low-latency / 0 B-frames
    # best-effort with a clean fallback if Configure rejects those keys.
    text = replace(
        text,
        'const DEFAULT_FPS: i32 = 30;\nconst DEFAULT_GOP_MS: i32 = 10_000;\n',
        'const DEFAULT_FPS: i32 = 60;\n'
        'const DEFAULT_GOP_MS: i32 = 10_000;\n'
        'const RTLD_NOW: c_int = 2;\n'
        'const RTLD_LOCAL: c_int = 0;\n',
    )
    text = replace(
        text,
        '    fn OH_AVFormat_SetLongValue(format: *mut c_void, key: *const c_char, value: i64) -> bool;\n',
        '    fn OH_AVFormat_SetLongValue(format: *mut c_void, key: *const c_char, value: i64) -> bool;\n'
        '    fn OH_AVFormat_SetDoubleValue(format: *mut c_void, key: *const c_char, value: f64) -> bool;\n'
        '    fn dlopen(filename: *const c_char, flags: c_int) -> *mut c_void;\n'
        '    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;\n',
    )
    text = replace(
        text,
        'OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_FRAME_RATE, DEFAULT_FPS)',
        'OH_AVFormat_SetDoubleValue(fmt, OH_MD_KEY_FRAME_RATE, DEFAULT_FPS as f64)',
        2,
    )
    text = replace(
        text,
        'fn mime_for(format: CodecFormat) -> Option<&\'static str> {\n',
        'struct LowLatencyKeys {\n'
        '    enable_low_latency: *const c_char,\n'
        '    enable_b_frame: *const c_char,\n'
        '    max_b_frames: *const c_char,\n'
        '}\n'
        '\n'
        'unsafe impl Send for LowLatencyKeys {}\n'
        'unsafe impl Sync for LowLatencyKeys {}\n'
        '\n'
        'static LOW_LATENCY_KEYS: std::sync::OnceLock<LowLatencyKeys> = std::sync::OnceLock::new();\n'
        '\n'
        'unsafe fn resolve_md_key(symbol: &[u8], fallback: &\'static [u8]) -> *const c_char {\n'
        '    for lib in [\n'
        '        b"libnative_media_codecbase.so\\0".as_slice(),\n'
        '        b"libnative_media_core.so\\0".as_slice(),\n'
        '    ] {\n'
        '        let handle = dlopen(lib.as_ptr() as *const c_char, RTLD_NOW | RTLD_LOCAL);\n'
        '        if !handle.is_null() {\n'
        '            let ptr = dlsym(handle, symbol.as_ptr() as *const c_char);\n'
        '            if !ptr.is_null() {\n'
        '                let key = *(ptr as *const *const c_char);\n'
        '                if !key.is_null() {\n'
        '                    return key;\n'
        '                }\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '    fallback.as_ptr() as *const c_char\n'
        '}\n'
        '\n'
        'fn low_latency_keys() -> &\'static LowLatencyKeys {\n'
        '    LOW_LATENCY_KEYS.get_or_init(|| unsafe {\n'
        '        LowLatencyKeys {\n'
        '            enable_low_latency: resolve_md_key(\n'
        '                b"OH_MD_KEY_VIDEO_ENABLE_LOW_LATENCY\\0",\n'
        '                b"video_enable_low_latency\\0",\n'
        '            ),\n'
        '            enable_b_frame: resolve_md_key(\n'
        '                b"OH_MD_KEY_VIDEO_ENCODER_ENABLE_B_FRAME\\0",\n'
        '                b"video_encoder_enable_b_frame\\0",\n'
        '            ),\n'
        '            max_b_frames: resolve_md_key(\n'
        '                b"OH_MD_KEY_VIDEO_ENCODER_MAX_B_FRAMES\\0",\n'
        '                b"video_encoder_max_b_frames\\0",\n'
        '            ),\n'
        '        }\n'
        '    })\n'
        '}\n'
        '\n'
        'fn build_encoder_format(\n'
        '    mime: &str,\n'
        '    width: i32,\n'
        '    height: i32,\n'
        '    bitrate: u32,\n'
        '    gop_ms: i32,\n'
        '    low_latency: bool,\n'
        ') -> ResultType<(*mut c_void, bool)> {\n'
        '    let fmt = unsafe { OH_AVFormat_CreateVideoFormat(mime.as_ptr() as _, width, height) };\n'
        '    if fmt.is_null() {\n'
        '        bail!("OH_AVFormat_CreateVideoFormat failed");\n'
        '    }\n'
        '    let ok = unsafe {\n'
        '        OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_PIXEL_FORMAT, AV_PIXEL_FORMAT_NV12)\n'
        '            && OH_AVFormat_SetIntValue(\n'
        '                fmt,\n'
        '                OH_MD_KEY_VIDEO_ENCODE_BITRATE_MODE,\n'
        '                BITRATE_MODE_CBR,\n'
        '            )\n'
        '            && OH_AVFormat_SetLongValue(fmt, OH_MD_KEY_BITRATE, bitrate as i64 * 1000)\n'
        '            && OH_AVFormat_SetDoubleValue(fmt, OH_MD_KEY_FRAME_RATE, DEFAULT_FPS as f64)\n'
        '            && OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_I_FRAME_INTERVAL, gop_ms)\n'
        '    };\n'
        '    if low_latency && ok {\n'
        '        let keys = low_latency_keys();\n'
        '        unsafe {\n'
        '            let _ = OH_AVFormat_SetIntValue(fmt, keys.enable_low_latency, 1);\n'
        '            let _ = OH_AVFormat_SetIntValue(fmt, keys.enable_b_frame, 0);\n'
        '            let _ = OH_AVFormat_SetIntValue(fmt, keys.max_b_frames, 0);\n'
        '        }\n'
        '    }\n'
        '    Ok((fmt, ok))\n'
        '}\n'
        '\n'
        'fn mime_for(format: CodecFormat) -> Option<&\'static str> {\n',
    )
    text = replace(
        text,
        '                let fmt = unsafe {\n'
        '                    OH_AVFormat_CreateVideoFormat(\n'
        '                        mime.as_ptr() as _,\n'
        '                        config.width as i32,\n'
        '                        config.height as i32,\n'
        '                    )\n'
        '                };\n'
        '                if fmt.is_null() {\n'
        '                    unsafe {\n'
        '                        OH_VideoEncoder_Destroy(codec);\n'
        '                    }\n'
        '                    bail!("OH_AVFormat_CreateVideoFormat failed");\n'
        '                }\n'
        '                let ok = unsafe {\n'
        '                    OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_PIXEL_FORMAT, AV_PIXEL_FORMAT_NV12)\n'
        '                        && OH_AVFormat_SetIntValue(\n'
        '                            fmt,\n'
        '                            OH_MD_KEY_VIDEO_ENCODE_BITRATE_MODE,\n'
        '                            BITRATE_MODE_CBR,\n'
        '                        )\n'
        '                        && OH_AVFormat_SetLongValue(\n'
        '                            fmt,\n'
        '                            OH_MD_KEY_BITRATE,\n'
        '                            bitrate as i64 * 1000,\n'
        '                        )\n'
        '                        && OH_AVFormat_SetDoubleValue(fmt, OH_MD_KEY_FRAME_RATE, DEFAULT_FPS as f64)\n'
        '                        && OH_AVFormat_SetIntValue(fmt, OH_MD_KEY_I_FRAME_INTERVAL, gop_ms)\n'
        '                };\n'
        '                let rc = if ok {\n'
        '                    unsafe { OH_VideoEncoder_Configure(codec, fmt) }\n'
        '                } else {\n'
        '                    AV_ERR_INVALID_STATE\n'
        '                };\n'
        '                unsafe {\n'
        '                    OH_AVFormat_Destroy(fmt);\n'
        '                }\n'
        '                if rc != AV_ERR_OK {\n'
        '                    log::error!("OH_VideoEncoder_Configure failed: {rc}");\n'
        '                    unsafe {\n'
        '                        OH_VideoEncoder_Destroy(codec);\n'
        '                    }\n'
        '                    bail!("OH_VideoEncoder_Configure failed: {rc}");\n'
        '                }\n',
        '                let mut low_latency = true;\n'
        '                let (fmt, ok) = match build_encoder_format(\n'
        '                    mime,\n'
        '                    config.width as i32,\n'
        '                    config.height as i32,\n'
        '                    bitrate,\n'
        '                    gop_ms,\n'
        '                    true,\n'
        '                ) {\n'
        '                    Ok(v) => v,\n'
        '                    Err(e) => {\n'
        '                        unsafe {\n'
        '                            OH_VideoEncoder_Destroy(codec);\n'
        '                        }\n'
        '                        return Err(e);\n'
        '                    }\n'
        '                };\n'
        '                let mut rc = if ok {\n'
        '                    unsafe { OH_VideoEncoder_Configure(codec, fmt) }\n'
        '                } else {\n'
        '                    AV_ERR_INVALID_STATE\n'
        '                };\n'
        '                unsafe {\n'
        '                    OH_AVFormat_Destroy(fmt);\n'
        '                }\n'
        '                if rc != AV_ERR_OK {\n'
        '                    log::warn!(\n'
        '                        "OH_VideoEncoder_Configure with low-latency hints failed: {rc}; retrying without hints"\n'
        '                    );\n'
        '                    low_latency = false;\n'
        '                    let (fmt, ok) = match build_encoder_format(\n'
        '                        mime,\n'
        '                        config.width as i32,\n'
        '                        config.height as i32,\n'
        '                        bitrate,\n'
        '                        gop_ms,\n'
        '                        false,\n'
        '                    ) {\n'
        '                        Ok(v) => v,\n'
        '                        Err(e) => {\n'
        '                            unsafe {\n'
        '                                OH_VideoEncoder_Destroy(codec);\n'
        '                            }\n'
        '                            return Err(e);\n'
        '                        }\n'
        '                    };\n'
        '                    rc = if ok {\n'
        '                        unsafe { OH_VideoEncoder_Configure(codec, fmt) }\n'
        '                    } else {\n'
        '                        AV_ERR_INVALID_STATE\n'
        '                    };\n'
        '                    unsafe {\n'
        '                        OH_AVFormat_Destroy(fmt);\n'
        '                    }\n'
        '                }\n'
        '                if rc != AV_ERR_OK {\n'
        '                    log::error!("OH_VideoEncoder_Configure failed: {rc}");\n'
        '                    unsafe {\n'
        '                        OH_VideoEncoder_Destroy(codec);\n'
        '                    }\n'
        '                    bail!("OH_VideoEncoder_Configure failed: {rc}");\n'
        '                }\n',
    )
    return replace(
        text,
        '                log::info!(\n'
        '                    "ohcodec encoder created: {}, {}x{}, bitrate {bitrate} kbps, gop {gop_ms} ms",\n'
        '                    mime.trim_end_matches(\'\\0\'),\n'
        '                    config.width,\n'
        '                    config.height\n'
        '                );\n',
        '                log::info!(\n'
        '                    "ohcodec encoder created: {}, {}x{}, fps {DEFAULT_FPS}, bitrate {bitrate} kbps, gop {gop_ms} ms, low_latency={low_latency}",\n'
        '                    mime.trim_end_matches(\'\\0\'),\n'
        '                    config.width,\n'
        '                    config.height\n'
        '                );\n',
    )


def patch_ohos_decoder(text):
    # A freshly reset buffer-mode OhCodecDecoder receiving a mid-stream P-frame
    # times out after BUFFER_WAIT (100ms) and returns Ok(false). Treat that wait
    # as pending output up to SURFACE_STALL (3s) so client.rs does not instantly
    # mark H.265/H.264 unsupported before the next keyframe arrives.
    text = replace(
        text,
        '    pub fn is_surface_mode(&self) -> bool {\n'
        '        !self.cleanup.window.is_null()\n'
        '    }\n',
        '    pub fn is_surface_mode(&self) -> bool {\n'
        '        !self.cleanup.window.is_null()\n'
        '    }\n\n'
        '    pub fn is_pending_output(&self) -> bool {\n'
        '        self.is_surface_mode() || self.pending_since.is_some()\n'
        '    }\n',
    )
    text = replace(
        text,
        '                let now = Instant::now();\n'
        '                if now >= deadline {\n'
        '                    return Ok(false);\n'
        '                }\n',
        '                let now = Instant::now();\n'
        '                if now >= deadline {\n'
        '                    let pending_since = self.pending_since.get_or_insert_with(Instant::now);\n'
        '                    if pending_since.elapsed() >= SURFACE_STALL {\n'
        '                        bail!("ohcodec buffer output stalled");\n'
        '                    }\n'
        '                    return Ok(false);\n'
        '                }\n',
    )
    return replace(
        text,
        '        };\n'
        '        let converted = copy_output(output.buffer, output.format, rgb);\n',
        '        };\n'
        '        self.pending_since = None;\n'
        '        let converted = copy_output(output.buffer, output.format, rgb);\n',
    )


def patch_scrap_codec(text):
    return replace(
        text,
        '    #[cfg(target_env = "ohos")]\n'
        '    pub fn pending_surface_output(&self) -> bool {\n'
        '        self.h264_ohcodec\n'
        '            .as_ref()\n'
        '            .map(|decoder| decoder.is_surface_mode())\n'
        '            .unwrap_or_else(|| {\n'
        '                self.h265_ohcodec\n'
        '                    .as_ref()\n'
        '                    .is_some_and(|decoder| decoder.is_surface_mode())\n'
        '            })\n'
        '    }\n',
        '    #[cfg(target_env = "ohos")]\n'
        '    pub fn pending_surface_output(&self) -> bool {\n'
        '        self.h264_ohcodec\n'
        '            .as_ref()\n'
        '            .map(|decoder| decoder.is_pending_output())\n'
        '            .unwrap_or_else(|| {\n'
        '                self.h265_ohcodec\n'
        '                    .as_ref()\n'
        '                    .is_some_and(|decoder| decoder.is_pending_output())\n'
        '            })\n'
        '    }\n',
    )


def patch_ohos_ffi_channel(text):
    # AVScreenCapture delivers continuous 20 ms stereo s16le PCM buffers.
    # Storing them in single-slot FrameRaw overwrote every buffer that arrived
    # between consumer polls, making controlled-side audio choppy. Use a
    # bounded 4-byte-aligned FIFO queue for audio while keeping FrameRaw for
    # latest-frame video.
    text = replace(
        text,
        'use lazy_static::lazy_static;\nuse std::ops::Not;\n',
        'use lazy_static::lazy_static;\nuse std::collections::VecDeque;\nuse std::ops::Not;\n',
    )
    text = replace(
        text,
        '    static ref AUDIO_RAW_INNER: Mutex<FrameRaw> =\n'
        '        Mutex::new(FrameRaw::new("audio-inner", MAX_AUDIO_FRAME_TIMEOUT));\n'
        '    static ref AUDIO_RAW_MIC: Mutex<FrameRaw> =\n'
        '        Mutex::new(FrameRaw::new("audio-mic", MAX_AUDIO_FRAME_TIMEOUT));\n',
        '    static ref AUDIO_RAW_INNER: Mutex<AudioQueue> =\n'
        '        Mutex::new(AudioQueue::new("audio-inner", MAX_AUDIO_FRAME_TIMEOUT));\n'
        '    static ref AUDIO_RAW_MIC: Mutex<AudioQueue> =\n'
        '        Mutex::new(AudioQueue::new("audio-mic", MAX_AUDIO_FRAME_TIMEOUT));\n',
    )
    text = replace(
        text,
        'const MAX_VIDEO_FRAME_TIMEOUT: Duration = Duration::from_millis(100);\n'
        'const MAX_AUDIO_FRAME_TIMEOUT: Duration = Duration::from_millis(1000);\n',
        'const MAX_VIDEO_FRAME_TIMEOUT: Duration = Duration::from_millis(100);\n'
        'const MAX_AUDIO_FRAME_TIMEOUT: Duration = Duration::from_millis(1000);\n'
        '// 240 ms of 48 kHz stereo s16le PCM (4-byte stereo-frame aligned).\n'
        'const MAX_AUDIO_QUEUE_BYTES: usize = 3840 * 12;\n',
    )
    text = replace(
        text,
        'pub fn set_video_raw_enable(enable: bool) {\n',
        'struct AudioQueue {\n'
        '    name: &\'static str,\n'
        '    buf: VecDeque<u8>,\n'
        '    last_update: Instant,\n'
        '    last_taken: Instant,\n'
        '    timeout: Duration,\n'
        '    enable: bool,\n'
        '}\n'
        '\n'
        'impl AudioQueue {\n'
        '    fn new(name: &\'static str, timeout: Duration) -> Self {\n'
        '        AudioQueue {\n'
        '            name,\n'
        '            buf: VecDeque::new(),\n'
        '            last_update: Instant::now(),\n'
        '            last_taken: Instant::now(),\n'
        '            timeout,\n'
        '            enable: false,\n'
        '        }\n'
        '    }\n'
        '\n'
        '    fn set_enable(&mut self, value: bool) {\n'
        '        self.enable = value;\n'
        '        self.buf.clear();\n'
        '    }\n'
        '\n'
        '    fn push(&mut self, data: &[u8]) {\n'
        '        if self.enable.not() || self.last_taken.elapsed() > UNCONSUMED_SKIP || data.is_empty() {\n'
        '            return;\n'
        '        }\n'
        '        if data.len() >= MAX_AUDIO_QUEUE_BYTES {\n'
        '            self.buf.clear();\n'
        '            let drop_head = ((data.len() - MAX_AUDIO_QUEUE_BYTES + 3) / 4) * 4;\n'
        '            if drop_head < data.len() {\n'
        '                self.buf.extend(&data[drop_head..]);\n'
        '            }\n'
        '        } else {\n'
        '            let total = self.buf.len() + data.len();\n'
        '            if total > MAX_AUDIO_QUEUE_BYTES {\n'
        '                let overflow = total - MAX_AUDIO_QUEUE_BYTES;\n'
        '                let drop_bytes = ((overflow + 3) / 4) * 4;\n'
        '                let actual_drop = drop_bytes.min(self.buf.len());\n'
        '                self.buf.drain(..actual_drop);\n'
        '            }\n'
        '            self.buf.extend(data);\n'
        '        }\n'
        '        self.last_update = Instant::now();\n'
        '    }\n'
        '\n'
        '    fn take(&mut self, dst: &mut Vec<u8>) -> Option<()> {\n'
        '        self.last_taken = Instant::now();\n'
        '        if self.enable.not() || self.buf.is_empty() {\n'
        '            return None;\n'
        '        }\n'
        '        if self.last_update.elapsed() > self.timeout {\n'
        '            log::trace!("Failed to take {} raw,timeout!", self.name);\n'
        '            self.buf.clear();\n'
        '            return None;\n'
        '        }\n'
        '        dst.clear();\n'
        '        dst.reserve(self.buf.len());\n'
        '        let (front, back) = self.buf.as_slices();\n'
        '        dst.extend_from_slice(front);\n'
        '        dst.extend_from_slice(back);\n'
        '        self.buf.clear();\n'
        '        Some(())\n'
        '    }\n'
        '}\n'
        '\n'
        'pub fn set_video_raw_enable(enable: bool) {\n',
    )
    text = replace(
        text,
        'pub fn push_audio_frame(data: &[u8], mic: bool) {\n'
        '    if mic {\n'
        '        AUDIO_RAW_MIC.lock().unwrap().update(data);\n'
        '    } else {\n'
        '        AUDIO_RAW_INNER.lock().unwrap().update(data);\n'
        '    }\n'
        '}\n',
        'pub fn push_audio_frame(data: &[u8], mic: bool) {\n'
        '    if mic {\n'
        '        AUDIO_RAW_MIC.lock().unwrap().push(data);\n'
        '    } else {\n'
        '        AUDIO_RAW_INNER.lock().unwrap().push(data);\n'
        '    }\n'
        '}\n',
    )
    return replace(
        text,
        'pub fn get_audio_raw(dst: &mut Vec<u8>, last: &mut Vec<u8>, mic: bool) -> Option<()> {\n'
        '    if mic {\n'
        '        AUDIO_RAW_MIC.lock().ok()?.take(dst, last)\n'
        '    } else {\n'
        '        AUDIO_RAW_INNER.lock().ok()?.take(dst, last)\n'
        '    }\n'
        '}\n',
        'pub fn get_audio_raw(dst: &mut Vec<u8>, _last: &mut Vec<u8>, mic: bool) -> Option<()> {\n'
        '    if mic {\n'
        '        AUDIO_RAW_MIC.lock().ok()?.take(dst)\n'
        '    } else {\n'
        '        AUDIO_RAW_INNER.lock().ok()?.take(dst)\n'
        '    }\n'
        '}\n',
    )


def patch_audio_service(text):
    # 1. Poll at 10 ms for both AVScreenCapture inner audio and voice-call mic
    #    (both deliver 20 ms buffers) instead of sleeping 100 ms on inner audio.
    # 2. Exclude target_env = "ohos" from the non-Android branch of send_f32 so
    #    OHOS does not double-encode and double-send every 20 ms batch.
    text = replace(
        text,
        '                if !got {\n'
        '                    // The capturer callback paces at 20 ms; poll close to it so\n'
        '                    // the call does not accumulate 100 ms batches.\n'
        '                    hbb_common::sleep(if ohos_mic.is_some() { 0.02 } else { 0.1 }).await;\n'
        '                }\n',
        '                if !got {\n'
        '                    // Both AVScreenCapture and the OHAudio call mic deliver 20 ms\n'
        '                    // buffers; poll at 10 ms so audio streams continuously without\n'
        '                    // 100 ms stalls or buffer drops.\n'
        '                    hbb_common::sleep(0.01).await;\n'
        '                }\n',
    )
    return replace(
        text,
        '    #[cfg(not(target_os = "android"))]\n'
        '    match encoder.encoder.encode_vec_float(data, data.len() * 6) {\n',
        '    #[cfg(not(any(target_os = "android", target_env = "ohos")))]\n'
        '    match encoder.encoder.encode_vec_float(data, data.len() * 6) {\n',
    )


PATCHES = {
    "src/flutter.rs": patch_flutter,
    "src/flutter_ffi.rs": patch_flutter_ffi,
    "src/ohos_ffi.rs": patch_ohos_ffi,
    "src/server.rs": patch_server,
    "src/server/connection.rs": patch_server_connection,
    "src/ui_interface.rs": patch_ui_interface,
    "src/keyboard.rs": patch_keyboard,
    "src/client.rs": patch_client,
    "src/ui_session_interface.rs": patch_ui_session_interface,
    "src/client/io_loop.rs": patch_client_io_loop,
    "src/ipc.rs": patch_ipc,
    "src/lang.rs": patch_lang,
    "libs/scrap/src/ohos/ohcodec.rs": patch_ohcodec,
    "libs/scrap/src/ohos/decoder.rs": patch_ohos_decoder,
    "libs/scrap/src/common/codec.rs": patch_scrap_codec,
    "libs/scrap/src/ohos/ffi.rs": patch_ohos_ffi_channel,
    "libs/scrap/src/ohos/screencap.rs": patch_ohos_screencap,
    "src/server/audio_service.rs": patch_audio_service,
}


def with_video_adapter(base_patch, video_patch):
    def patch(text):
        return video_patch(base_patch(text), replace)
    return patch


for relative, video_patch in flutter_video.PATCHES.items():
    PATCHES[relative] = with_video_adapter(PATCHES[relative], video_patch)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    proc = subprocess.run(
        ['git', '--no-replace-objects', '-C', str(root), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode:
        raise ValueError(
            f"git {' '.join(args)} failed: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout


def checked_path(root, relative):
    path = root
    for component in Path(relative).parts:
        path = path / component
        if path.is_symlink():
            raise ValueError(f"refusing symlink: {path}")
    if not path.is_file():
        raise ValueError(f"missing source file: {path}")
    return path


def publish(path, before, after):
    # Do not overwrite a concurrent build/integration edit after preflight.
    current = path.read_bytes() if path.exists() else None
    if path.is_symlink() or current != before:
        raise ValueError(f"file changed after preflight: {path}")
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.ohos-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(after)
        os.chmod(temporary, mode)
        current = path.read_bytes() if path.exists() else None
        if path.is_symlink() or current != before:
            raise ValueError(f"file changed before publication: {path}")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('core_root', type=Path)
    parser.add_argument('--check', action='store_true',
                        help='validate all inputs and show states without changing files')
    args = parser.parse_args()
    root = args.core_root.resolve(strict=True)
    top = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if root != top:
        raise ValueError(f"CORE_ROOT must be the checkout root, got {root}, expected {top}")
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if head != CORE_SHA:
        raise ValueError(f"unsupported core HEAD {head}; expected {CORE_SHA}")
    hbb_head = git(root / 'libs/hbb_common', 'rev-parse', 'HEAD').decode().strip()
    if hbb_head != HBB_SHA:
        raise ValueError(
            f"unsupported hbb_common HEAD {hbb_head}; expected the author's gitlink {HBB_SHA}")

    changes = []
    for relative, source_hash in SOURCE_HASHES.items():
        path = checked_path(root, relative)
        original = git(root, 'show', f'{CORE_SHA}:{relative}')
        if sha256(original) != source_hash:
            raise ValueError(f"unexpected Git source hash: {relative}")
        try:
            patched = PATCHES[relative](original.decode('utf-8')).encode('utf-8')
        except ValueError as error:
            raise ValueError(f"{relative}: {error}") from error
        current = path.read_bytes()
        if current not in (original, patched):
            raise ValueError(
                f"unknown worktree source: {relative}, SHA256={sha256(current)}")
        state = 'already applied' if current == patched else 'would update'
        changes.append((relative, path, current, patched, state))

    repo_root = Path(__file__).resolve().parents[2]
    for relative in COPY_FILES:
        src_path = checked_path(repo_root, relative)
        patched = src_path.read_bytes()
        dst_path = root
        for component in Path(relative).parts:
            dst_path = dst_path / component
            if dst_path.is_symlink():
                raise ValueError(f"refusing symlink: {dst_path}")
        current = dst_path.read_bytes() if dst_path.exists() else None
        state = 'already applied' if current == patched else 'would update'
        changes.append((relative, dst_path, current, patched, state))

    # No writes have happened unless every source and every patch anchor passed.
    for relative, path, current, patched, state in changes:
        if not args.check and current != patched:
            publish(path, current, patched)
            state = 'updated'
        print(f'{state}: {relative} SHA256={sha256(patched)}', flush=True)
    print('Preflight complete; no files written.' if args.check else 'Core patch complete.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f'core_patch: {error}', file=sys.stderr)
        sys.exit(1)
