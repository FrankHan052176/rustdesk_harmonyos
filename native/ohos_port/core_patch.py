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

CORE_SHA = "d53391e9b0b87a94c5ee1f8f40252acb25867395"
HBB_SHA = "75ef5d8cd9b2e817047fb2eb4c2a1cdac7141dbf"
SOURCE_HASHES = {
    "src/flutter.rs": "c7a377869fc6dad88d7e8afe292cf95eaa0358509e4eab03ffb169b3022bddde",
    "src/flutter_ffi.rs": "400191f6761117e67562a68bb02cf0adb834e8e01134cbfc47ed0b634fa9d607",
    "src/ohos_ffi.rs": "48495c87af79d3a39a0efff918bee1d908bf817e70c715f4921649e846e16335",
    "src/server/connection.rs": "ab3277c768610219580381fe244197c0a4dcde3d8040d71b5bb897d5b63d4bd9",
    "src/ui_interface.rs": "0d26ed8a0826e8985184e139459d7efa92dc2ec602c1ba88d3a48e173a6c92e9",
    "src/keyboard.rs": "64357790e34d85a23a6d18f5eecb05e13e89de51b599060c7f5a6f55a03707a8",
    "src/client.rs": "8cf52adb3e4af4c85862fdac8f02e8a018fa62f755fe927fb9b785c621f7a744",
    "src/ui_session_interface.rs": "e5a0366e42f229e2ef25910bb2768a61850b97208ce5f8cba6f539e099470f52",
    "src/client/io_loop.rs": "bba395026cb5d7e691807431b0e2700d3245700558642e7de9b679ccf9590c42",
    "src/ipc.rs": "553407334d95d397d94865599a683165cc337161d4c43a37d0c86ff221e67cf5",
    "src/lang.rs": "ba445488ddf44cfafc349c2d5d901f1c6869cfea839a083d7e20a3328d603c1a",
}

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
    return replace(
        text, 'impl InvokeUiSession for OhosHandler {\n',
        'impl InvokeUiSession for OhosHandler {\n'
        '    #[cfg(feature = "flutter")]\n'
        '    fn is_multi_ui_session(&self) -> bool {\n        false\n    }\n\n',
    )


def patch_server_connection(text):
    # With `flutter` the CM UI is in-process, so the Flutter channel replaces the
    # ArkTS one; keeping both imports would be a duplicate `start_channel`. The
    # call site is already gated on android/ios/ohos.
    return replace(
        text,
        MOBILE_CFG + '\nuse crate::{common::DEVICE_NAME, flutter::connection_manager::start_channel};\n'
        '#[cfg(target_env = "ohos")]\n'
        'use crate::{common::DEVICE_NAME, ohos_ffi::connection_manager::start_channel};',
        '#[cfg(any(target_os = "android", target_os = "ios", all(target_env = "ohos", feature = "flutter")))]\n'
        'use crate::{common::DEVICE_NAME, flutter::connection_manager::start_channel};\n'
        '#[cfg(all(target_env = "ohos", not(feature = "flutter")))]\n'
        'use crate::{common::DEVICE_NAME, ohos_ffi::connection_manager::start_channel};',
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


PATCHES = {
    "src/flutter.rs": patch_flutter,
    "src/flutter_ffi.rs": patch_flutter_ffi,
    "src/ohos_ffi.rs": patch_ohos_ffi,
    "src/server/connection.rs": patch_server_connection,
    "src/ui_interface.rs": patch_ui_interface,
    "src/keyboard.rs": patch_keyboard,
    "src/client.rs": patch_client,
    "src/ui_session_interface.rs": patch_ui_session_interface,
    "src/client/io_loop.rs": patch_client_io_loop,
    "src/ipc.rs": patch_ipc,
    "src/lang.rs": patch_lang,
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
    mode = path.stat().st_mode & 0o777
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
