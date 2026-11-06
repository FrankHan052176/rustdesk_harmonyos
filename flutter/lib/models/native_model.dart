import 'dart:async';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:device_info_plus/device_info_plus.dart';
import 'package:ffi/ffi.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/consts.dart';
import 'package:flutter_hbb/main.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';

import '../common.dart';
import '../generated_bridge.dart';
import '../utils/ohos_session_window.dart';

final class RgbaFrame extends Struct {
  @Uint32()
  external int len;
  external Pointer<Uint8> data;
}

typedef F3 = Pointer<Uint8> Function(Pointer<Utf8>, int);
typedef F3Dart = Pointer<Uint8> Function(Pointer<Utf8>, Int32);
typedef HandleEvent = Future<void> Function(Map<String, dynamic> evt);
// The controlled-side C ABI the OpenHarmony core exports (the same entry points
// its own ArkTS app uses), reached through the loaded library instead of FRB.
typedef _OhosInitC = Int32 Function(
    Pointer<Utf8>, Pointer<Utf8>, Pointer<Utf8>);
typedef _OhosInitDart = int Function(
    Pointer<Utf8>, Pointer<Utf8>, Pointer<Utf8>);
typedef _OhosServerStartC = Int32 Function();
typedef _OhosServerStartDart = int Function();
typedef _OhosServerServiceC = Int32 Function();
typedef _OhosServerServiceDart = int Function();
typedef _OhosCaptureStartC = Int32 Function(Int32, Int32);
typedef _OhosCaptureStartDart = int Function(int, int);
typedef _OhosCaptureStopC = Int32 Function();
typedef _OhosCaptureStopDart = int Function();
typedef _OhosCaptureRunningC = Int32 Function();
typedef _OhosCaptureRunningDart = int Function();

/// The Linux bundle keeps the core library at lib/librustdesk.so next to the
/// executable. Prefer that copy, mirroring flutter/linux/main.cc: the plain
/// name relies on the loader search path, which repackaged installs may not
/// cover. https://github.com/rustdesk/rustdesk/discussions/14407
DynamicLibrary _openLinuxCoreLib() {
  final bundled =
      '${File(Platform.resolvedExecutable).parent.path}/lib/librustdesk.so';
  try {
    if (File(bundled).existsSync()) {
      return DynamicLibrary.open(bundled);
    }
  } catch (e) {
    debugPrint("Failed to load '$bundled': $e");
  }
  return DynamicLibrary.open('librustdesk.so');
}

/// FFI wrapper around the native Rust core.
/// Hides the platform differences.
class PlatformFFI {
  static String _ohosLocaleName = '';
  String _dir = '';
  // _homeDir is only needed for Android and IOS.
  String _homeDir = '';
  final _eventHandlers = <String, Map<String, HandleEvent>>{};
  late RustdeskImpl _ffiBind;
  late String _appType;
  StreamEventHandler? _eventCallback;
  StreamSubscription<String>? _globalEventSubscription;
  Future<void>? _windowCleanup;

  PlatformFFI._();

  static final PlatformFFI instance = PlatformFFI._();
  final _toAndroidChannel = const MethodChannel('mChannel');
  final _toOhosChannel =
      const MethodChannel('top.frankhan.resk.flutter/platform');

  RustdeskImpl get ffiBind => _ffiBind;
  @visibleForTesting
  void setBindingForTesting(RustdeskImpl binding) => _ffiBind = binding;

  F3? _session_get_rgba;
  late final _OhosInitDart _ohosInit;
  late final _OhosServerStartDart _ohosServerStart;
  late final _OhosServerServiceDart _ohosServerStartService;
  late final _OhosServerServiceDart _ohosServerStopService;
  late final _OhosCaptureStartDart _ohosCaptureStart;
  late final _OhosCaptureStopDart _ohosCaptureStop;
  late final _OhosCaptureRunningDart _ohosCaptureRunning;

  /// Whether the OpenHarmony core is capturing the screen for the controlled side.
  ///
  /// Capture counts as running from the moment the request is submitted, so this
  /// cannot tell a pending consent dialog apart from a granted capture.
  bool get isOhosHostCaptureRunning => isOhos && _ohosCaptureRunning() != 0;

  static String get localeName => isOhos && _ohosLocaleName.isNotEmpty
      ? _ohosLocaleName
      : Platform.localeName;

  static get isMain => instance._appType == kAppTypeMain;

  static String getByName(String name, [String arg = '']) {
    return '';
  }

  static void setByName(String name, [String value = '']) {}

  static Future<String> getVersion() async {
    if (isOhos) {
      return await instance._ffiBind.mainGetVersion();
    }
    PackageInfo packageInfo = await PackageInfo.fromPlatform();
    return packageInfo.version;
  }

  bool registerEventHandler(
      String eventName, String handlerName, HandleEvent handler, {bool replace = false}) {
    debugPrint('registerEventHandler $eventName $handlerName');
    var handlers = _eventHandlers[eventName];
    if (handlers == null) {
      _eventHandlers[eventName] = {handlerName: handler};
      return true;
    } else {
      if (!replace && handlers.containsKey(handlerName)) {
        return false;
      } else {
        handlers[handlerName] = handler;
        return true;
      }
    }
  }

  void unregisterEventHandler(String eventName, String handlerName) {
    debugPrint('unregisterEventHandler $eventName $handlerName');
    var handlers = _eventHandlers[eventName];
    if (handlers != null) {
      handlers.remove(handlerName);
    }
  }

  String translate(String name, String locale) =>
      _ffiBind.translate(name: name, locale: locale);

  Uint8List? getRgba(SessionID sessionId, int display, int bufSize) {
    if (_session_get_rgba == null) return null;
    final sessionIdStr = sessionId.toString();
    var a = sessionIdStr.toNativeUtf8();
    try {
      final buffer = _session_get_rgba!(a, display);
      if (buffer == nullptr) {
        return null;
      }
      final data = buffer.asTypedList(bufSize);
      return data;
    } finally {
      malloc.free(a);
    }
  }

  int getRgbaSize(SessionID sessionId, int display) =>
      _ffiBind.sessionGetRgbaSize(sessionId: sessionId, display: display);
  void nextRgba(SessionID sessionId, int display) =>
      _ffiBind.sessionNextRgba(sessionId: sessionId, display: display);
  void registerPixelbufferTexture(SessionID sessionId, int display, int ptr) {
    if (isOhos) return;
    _ffiBind.sessionRegisterPixelbufferTexture(
        sessionId: sessionId, display: display, ptr: ptr);
  }

  void registerGpuTexture(SessionID sessionId, int display, int ptr) {
    if (isOhos) return;
    _ffiBind.sessionRegisterGpuTexture(
        sessionId: sessionId, display: display, ptr: ptr);
  }

  /// Init the FFI class, loads the native Rust core library.
  Future<void> init(String appType) async {
    _appType = appType;
    final dylib = isOhos
        ? DynamicLibrary.open('liblibrustdesk.so')
        : isAndroid
            ? DynamicLibrary.open('librustdesk.so')
            : isLinux
                ? _openLinuxCoreLib()
                : isWindows
                    ? DynamicLibrary.open('librustdesk.dll')
                    :
                    // Use executable itself as the dynamic library for MacOS.
                    // Multiple dylib instances will cause some global instances to be invalid.
                    // eg. `lazy_static` objects in rust side, will be created more than once, which is not expected.
                    //
                    // isMacOS? DynamicLibrary.open("liblibrustdesk.dylib") :
                    DynamicLibrary.process();
    debugPrint('initializing FFI $_appType');
    try {
      _session_get_rgba = dylib.lookupFunction<F3Dart, F3>("session_get_rgba");
      if (isOhos) {
        // The controlled-side C ABI the OpenHarmony core exports for its own UI.
        _ohosInit =
            dylib.lookupFunction<_OhosInitC, _OhosInitDart>("ohos_init");
        _ohosServerStart =
            dylib.lookupFunction<_OhosServerStartC, _OhosServerStartDart>(
                "ohos_server_start");
        _ohosServerStartService =
            dylib.lookupFunction<_OhosServerServiceC, _OhosServerServiceDart>(
                "ohos_server_start_service");
        _ohosServerStopService =
            dylib.lookupFunction<_OhosServerServiceC, _OhosServerServiceDart>(
                "ohos_server_stop_service");
        _ohosCaptureStart =
            dylib.lookupFunction<_OhosCaptureStartC, _OhosCaptureStartDart>(
                "ohos_capture_start");
        _ohosCaptureStop =
            dylib.lookupFunction<_OhosCaptureStopC, _OhosCaptureStopDart>(
                "ohos_capture_stop");
        _ohosCaptureRunning = dylib.lookupFunction<_OhosCaptureRunningC,
            _OhosCaptureRunningDart>("ohos_capture_running");
      }
      try {
        if (isOhos) {
          _dir = await _toOhosChannel.invokeMethod<String>('getFilesDir') ?? '';
          ohosDeviceType =
              await _toOhosChannel.invokeMethod<String>('getDeviceType') ?? '';
          _ohosLocaleName =
              await _toOhosChannel.invokeMethod<String>('getSystemLocale') ??
                  '';
          _toOhosChannel.setMethodCallHandler(_handleOhosWindowCall);
          _applyOhosWindowStatus(
              await _toOhosChannel.invokeMethod<String>('watchWindowStatus'));
          // A free-form window on a tablet keeps its caption as well, so every OHOS window
          // gets the same framing preparation.
          await _toOhosChannel.invokeMethod<void>('prepareWindow');
        } else {
          // SYSTEM user failed
          _dir = (await getApplicationDocumentsDirectory()).path;
        }
      } catch (e) {
        debugPrint('Failed to get documents directory: $e');
      }
      if (_dir.isEmpty) {
        throw StateError('Application files directory is unavailable');
      }
      if (isOhos) {
        // The core reads $HOME on OpenHarmony (hbb_common's APP_HOME_DIR is
        // android/ios-only), so its home is a dedicated subdirectory of the
        // sandbox and has to exist before the first native call.
        _homeDir = '$_dir/home';
        await Directory(_homeDir).create(recursive: true);
        final home = _homeDir.toNativeUtf8();
        final appDir = _dir.toNativeUtf8();
        final customClientConfig = ''.toNativeUtf8();
        _ohosInit(home, appDir, customClientConfig);
        malloc.free(home);
        malloc.free(appDir);
        malloc.free(customClientConfig);
      }
      _ffiBind = RustdeskImpl(dylib);

      if (isLinux) {
        if (isMain) {
          // Start a dbus service for uri links, no need to await
          _ffiBind.mainStartDbusServer();
        }
      } else if (isMacOS && isMain) {
        // Start ipc service for uri links.
        _ffiBind.mainStartIpcUrlServer();
      }
      _startListenEvent(_ffiBind); // global event
      try {
        if (isAndroid) {
          // Android file transfer uses app-specific storage. User-selected
          // files enter and leave this workspace through the system picker.
          _homeDir = (await getExternalStorageDirectory())?.path ??
              (await getApplicationSupportDirectory()).path;
        } else if (isIOS) {
          // The previous code was `_homeDir = (await getDownloadsDirectory())?.path ?? '';`,
          // which provided the `downloads` path in the sandbox.
          // It is unclear why we now use the `data` directory in the sandbox instead.
          _homeDir = _ffiBind.mainGetDataDirIos(appDir: _dir);
        } else {
          // no need to set home dir
        }
      } catch (e) {
        debugPrintStack(label: 'initialize failed: $e');
      }
      String id = 'NA';
      String name = 'Flutter';
      DeviceInfoPlugin deviceInfo = DeviceInfoPlugin();
      if (isAndroid) {
        AndroidDeviceInfo androidInfo = await deviceInfo.androidInfo;
        name = '${androidInfo.brand}-${androidInfo.model}';
        id = androidInfo.id.hashCode.toString();
        androidVersion = androidInfo.version.sdkInt;
      } else if (isIOS) {
        IosDeviceInfo iosInfo = await deviceInfo.iosInfo;
        name = iosInfo.utsname.machine;
        id = iosInfo.identifierForVendor.hashCode.toString();
      } else if (isOhos) {
        name = Platform.localHostname;
        id = name.hashCode.toString();
      } else if (isLinux) {
        LinuxDeviceInfo linuxInfo = await deviceInfo.linuxInfo;
        name = linuxInfo.name;
        id = linuxInfo.machineId ?? linuxInfo.id;
      } else if (isWindows) {
        try {
          // request windows build number to fix overflow on win7
          windowsBuildNumber = getWindowsTargetBuildNumber();
          WindowsDeviceInfo winInfo = await deviceInfo.windowsInfo;
          name = winInfo.computerName;
          id = winInfo.computerName;
        } catch (e) {
          debugPrintStack(label: "get windows device info failed: $e");
          name = "unknown";
          id = "unknown";
        }
      } else if (isMacOS) {
        MacOsDeviceInfo macOsInfo = await deviceInfo.macOsInfo;
        name = macOsInfo.computerName;
        id = macOsInfo.systemGUID ?? '';
      }
      if (isAndroid || isIOS || isOhos) {
        debugPrint(
            '_appType:$_appType,info1-id:$id,info2-name:$name,dir:$_dir,homeDir:$_homeDir');
      } else {
        debugPrint(
            '_appType:$_appType,info1-id:$id,info2-name:$name,dir:$_dir');
      }
      if (desktopType == DesktopType.cm) {
        await _ffiBind.cmInit();
      }
      await _ffiBind.mainDeviceId(id: id);
      await _ffiBind.mainDeviceName(name: name);
      await _ffiBind.mainSetHomeDir(home: _homeDir);
      await _ffiBind.mainInit(
        appDir: _dir,
        customClientConfig: '',
      );
      if (isOhos) {
        // Like the OpenHarmony app itself: register with the rendezvous server as
        // soon as the app opens, so this device's ID resolves before sharing.
        _ohosServerStart();
      }
    } catch (e) {
      debugPrintStack(label: 'initialize failed: $e');
    }
    version = await getVersion();
  }

  Future<bool> tryHandle(Map<String, dynamic> evt) async {
    final name = evt['name'];
    if (name != null) {
      final handlers = _eventHandlers[name];
      if (handlers != null) {
        if (handlers.isNotEmpty) {
          for (var handler in handlers.values) {
            await handler(evt);
          }
          return true;
        }
      }
    }
    return false;
  }

  /// Start listening to the Rust core's events and frames.
  void _startListenEvent(RustdeskImpl rustdeskImpl) {
    final appType =
        _appType == kAppTypeDesktopRemote ? '$_appType,$kWindowId' : _appType;
    var sink = rustdeskImpl.startGlobalEventStream(appType: appType);
    _globalEventSubscription = sink.listen((message) {
      () async {
        try {
          Map<String, dynamic> event = json.decode(message);
          // _tryHandle here may be more flexible than _eventCallback
          if (!await tryHandle(event)) {
            if (_eventCallback != null) {
              await _eventCallback!(event);
            }
          }
        } catch (e) {
          debugPrint('json.decode fail(): $e');
        }
      }();
    });
  }

  void setEventCallback(StreamEventHandler fun) async {
    _eventCallback = fun;
  }

  Future<Map<String, dynamic>?> getWindowLaunchPayload() =>
      _toOhosChannel
          .invokeMapMethod<String, dynamic>('getWindowLaunchPayload');

  Future<void> openSessionWindow(Map<String, dynamic> payload, String title) async {
    await _toOhosChannel.invokeMethod<String>('openSessionWindow', {
      'payload': jsonEncode(payload),
      'title': title,
    });
  }

  Future<void> prepareSessionWindow() =>
      _toOhosChannel.invokeMethod<void>('prepareWindow');

  Future<void> _cleanupSessionWindow() =>
      _windowCleanup ??= _finishSessionWindow();

  Future<void> _finishSessionWindow() async {
    await OhosSessionWindow.close();
    if (_globalEventSubscription != null) {
      await _ffiBind.stopGlobalEventStream(appType: _appType);
      await _globalEventSubscription!.cancel();
      _globalEventSubscription = null;
    }
  }

  void setRgbaCallback(void Function(int, Uint8List) fun) async {}

  // web only
  void setCursorDataCallback(
      void Function(String, int, int, int, int, Uint8List) fun) async {}

  // web only, decoded WebCodecs frames arriving as ready-made images
  void setVideoFrameCallback(
      Future<void> Function(int, ui.Image, bool Function()) fun) {}

  void clearVideoFrameCallback() {}

  void startDesktopWebListener() {}

  void stopDesktopWebListener() {}

  Future<List<String>> getSoundInputs() async {
    if (isOhos) {
      return (await _toOhosChannel
                  .invokeListMethod<String>('getAudioInputDevices') ??
              <String>[])
          .where((device) => device.isNotEmpty)
          .toList();
    }
    return (await _ffiBind.mainGetSoundInputs())
        .where((device) => device.isNotEmpty)
        .toList();
  }

  Future<void> selectSoundInput(String device) async {
    if (!isOhos) return;
    await _toOhosChannel
        .invokeMethod<void>('selectAudioInputDevice', {'device': device});
  }

  Future<bool> isWindowMaximized() async {
    if (!isOhos) return false;
    return await _toOhosChannel.invokeMethod<bool>('isWindowMaximized') ??
        false;
  }

  Future<void> minimizeWindow() async {
    if (!isOhos) return;
    await _toOhosChannel.invokeMethod<void>('minimizeWindow');
  }

  Future<bool> toggleMaximizeWindow() async {
    if (!isOhos) return false;
    return await _toOhosChannel.invokeMethod<bool>('toggleMaximizeWindow') ??
        false;
  }

  Future<void> startMovingWindow() async {
    if (!isOhos) return;
    await _toOhosChannel.invokeMethod<void>('startMovingWindow');
  }

  Future<void> setKeepScreenOn(bool enabled) async {
    if (!isOhos) return;
    await _toOhosChannel
        .invokeMethod<void>('setKeepScreenOn', {'enabled': enabled});
  }

  /// Native fullscreen for the main window or a 2in1 session window.
  Future<void> setFullscreen(bool enabled) async {
    if (!isOhos) return;
    await _toOhosChannel
        .invokeMethod<void>('setFullscreen', {'enabled': enabled});
  }

  Future<String> getOhosDownloadDirectory() async {
    if (!isOhos) {
      throw UnsupportedError('HarmonyOS Download directory is unavailable');
    }
    final directory =
        await _toOhosChannel.invokeMethod<String>('getDownloadDirectory');
    if (directory == null || directory.isEmpty) {
      throw StateError('Application Download directory is unavailable');
    }
    return directory;
  }

  /// Window lifecycle the platform bridge reports for the main or a session
  /// window.
  Future<bool> _handleOhosWindowCall(MethodCall call) async {
    switch (call.method) {
      case 'onWindowClose':
        if (!OhosSessionWindow.isSession) return false;
        await _cleanupSessionWindow();
        return true;
      case 'onWindowStatus':
        _applyOhosWindowStatus((call.arguments as Map)['status'] as String?);
        return true;
      default:
        return false;
    }
  }

  /// Starts the controlled side through the OpenHarmony core's own C ABI, in
  /// the order its ArkTS app uses: the rendezvous mediator first, then the
  /// continuous task, then screen capture at the display size, and only after a
  /// successful capture start does the mediator keep running. The capture
  /// consent dialog is a system dialog the core raises itself.
  Future<String> startOhosHost() async {
    if (!isOhos) return 'OHOS host is unavailable';
    if (_ohosServerStart() != 0) {
      return 'Failed to start the HarmonyOS server';
    }
    Map<String, dynamic>? displayInfo;
    try {
      displayInfo = await _toOhosChannel
          .invokeMapMethod<String, dynamic>('getDefaultDisplayInfo');
    } catch (error) {
      return 'Failed to read the HarmonyOS display: $error';
    }
    final width = displayInfo?['width'] as int? ?? 0;
    final height = displayInfo?['height'] as int? ?? 0;
    try {
      // The notification consent of the continuous task has to be answered
      // before the capture consent dialog pops, so the two never stack.
      await _toOhosChannel.invokeMethod<void>('startContinuousTask');
    } catch (error) {
      return 'Failed to start the HarmonyOS continuous task: $error';
    }
    if (_ohosCaptureStart(width, height) != 0) {
      await _toOhosChannel.invokeMethod<void>('stopContinuousTask');
      return 'Failed to start the HarmonyOS screen capture';
    }
    _ohosServerStartService();
    return '';
  }

  /// Stops capture first and the mediator second, like the OpenHarmony app.
  Future<String> stopOhosHost() async {
    if (!isOhos) return '';
    _ohosCaptureStop();
    _ohosServerStopService();
    try {
      await _toOhosChannel.invokeMethod<void>('stopContinuousTask');
    } catch (error) {
      return 'Failed to stop the HarmonyOS continuous task: $error';
    }
    return '';
  }

  Future<void> closeWindow() async {
    if (!isOhos) return;
    if (OhosSessionWindow.isSession) {
      // A failed teardown still leaves the user a way out of a stuck window.
      try {
        await _cleanupSessionWindow();
      } catch (error) {
        debugPrint('OHOS session cleanup failed (${error.runtimeType})');
      }
    }
    await _toOhosChannel.invokeMethod<void>('closeWindow');
  }

  /// Closes the HarmonyOS window without running the session teardown first, for callers
  /// that are already inside it.
  Future<void> terminateWindow() async {
    if (!isOhos) return;
    await _toOhosChannel.invokeMethod<void>('closeWindow');
  }

  /// Applies the HarmonyOS window mode; a free-form window uses the desktop UI.
  void _applyOhosWindowStatus(String? status) {
    if (!isOhos) return;
    final freeform = status == 'floating';
    debugPrint('OHOS window mode: ${status ?? 'unknown'}, freeform=$freeform');
    if (ohosFreeformWindow.value != freeform) {
      ohosFreeformWindow.value = freeform;
    }
  }

  void setMethodCallHandler(FMethod callback) {
    _toAndroidChannel.setMethodCallHandler((call) async {
      callback(call.method, call.arguments);
      return null;
    });
  }

  invokeMethod(String method, [dynamic arguments]) async {
    if (isOhos && method == 'enable_soft_keyboard') {
      // The Android channel owns the soft-keyboard hint; on HarmonyOS the session only
      // needs the input method to really go away when it hides the keyboard. Leaving the
      // IME up also kept the keyboard inset, which lifted the bottom bar.
      if (arguments == false) {
        // clearClient drops the input connection, which is what actually dismisses the
        // HarmonyOS input method; hide() alone leaves it on screen.
        debugPrint('OHOS soft keyboard: dismissing the input method');
        await SystemChannels.textInput.invokeMethod<void>('TextInput.clearClient');
        await SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
      }
      return true;
    }
    if (!isAndroid) return Future<bool>(() => false);
    return await _toAndroidChannel.invokeMethod(method, arguments);
  }

  Future<T?> invokeMethodWithResult<T>(String method,
      [dynamic arguments]) async {
    if (!isAndroid) return null;
    return await _toAndroidChannel.invokeMethod<T>(method, arguments);
  }

  void syncAndroidServiceAppDirConfigPath() {
    invokeMethod(AndroidChannel.kSyncAppDirConfigPath, _dir);
  }

  void setFullscreenCallback(void Function(bool) fun) {}
}
