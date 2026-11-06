import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_hbb/models/model.dart';

/// One display of one session, as the core has to register it.
@immutable
class OhosVideoTarget {
  const OhosVideoTarget({
    required this.sessionId,
    required this.display,
    required this.width,
    required this.height,
  });

  final String sessionId;
  final int display;
  final int width;
  final int height;

  /// A decoder cannot be configured for a display without a real remote size.
  bool get isValid => width > 0 && height > 0;

  @override
  bool operator ==(Object other) =>
      other is OhosVideoTarget &&
      other.sessionId == sessionId &&
      other.display == display &&
      other.width == width &&
      other.height == height;

  @override
  int get hashCode => Object.hash(sessionId, display, width, height);

  @override
  String toString() =>
      'OhosVideoTarget($sessionId display:$display ${width}x$height)';
}

/// The display the session page shows, or null while there is nothing to
/// register: peer info may not have arrived yet, and "all displays" is one
/// picture but not one Surface.
OhosVideoTarget? ohosVideoTargetOf(FFI ffi) {
  final pi = ffi.ffiModel.pi;
  if (!pi.isSet.value) return null;
  final display = pi.currentDisplay;
  final d = pi.tryGetDisplayIfNotAllDisplay();
  if (d == null) return null;
  final target = OhosVideoTarget(
    sessionId: ffi.sessionId.toString(),
    display: display,
    width: d.width,
    height: d.height,
  );
  return target.isValid ? target : null;
}

/// The session's external video texture on HarmonyOS.
///
/// A hardware-decoded H.264/H.265 display has its decoder write straight into
/// the Surface behind this texture, so no frame is copied through the CPU. One
/// texture belongs to one session and is owned only here: this object creates
/// it, binds it to the display the page shows, unbinds it when the page, the
/// display, or the app goes away, and destroys it only after the core has
/// confirmed that nothing can paint into it any more.
///
/// Every step is serialized, because each registration makes the core create a
/// new decoder generation for that Surface: a binding that overtook the
/// unbinding before it would leave the decoder and the registration describing
/// different displays.
class OhosVideoTextureModel extends ChangeNotifier with WidgetsBindingObserver {
  OhosVideoTextureModel({
    required this.sessionId,
    required this.onSurfaceFrame,
  }) {
    WidgetsBinding.instance.addObserver(this);
  }

  /// The HarmonyOS channel that creates, binds and destroys video textures.
  static const platformChannel =
      MethodChannel('top.frankhan.resk.flutter/platform');

  /// The session whose Surface decoders paint into the texture.
  final String sessionId;

  /// Called on every frame the core reports really came out of a Surface.
  ///
  /// A hardware-decoded display produces no memory frame, so this is the only
  /// first image there is; the session's existing first-image work runs here.
  final Future<void> Function() onSurfaceFrame;

  int _textureId = -1;

  /// The platform's texture, or -1 before one exists.
  int get textureId => _textureId;

  OhosVideoTarget? _desired;
  OhosVideoTarget? _bound;
  int? _binding;

  /// The mode the core last reported for [_bound] or [_binding].
  bool _surfaceOutput = false;
  bool _paused = false;
  bool _closing = false;
  bool _detachFailed = false;
  Future<void> _tail = Future<void>.value();
  Future<void>? _closeFuture;

  /// Whether [display]'s picture comes out of the texture right now.
  ///
  /// Only a frame the core reported can say so: neither a successful
  /// registration nor the codec proves that anything reached the screen.
  bool isSurfaceOutput(int display) =>
      !_closing &&
      _surfaceOutput &&
      (_bound?.display ?? _binding) == display;

  /// Declares what the session page shows.
  ///
  /// Called on every build; an unchanged target never reaches the core, so an
  /// ordinary rebuild cannot re-register the Surface and rebuild the decoder
  /// for nothing.
  void showTarget(OhosVideoTarget? target) {
    if (_closing) return;
    final next = target != null && target.isValid ? target : null;
    if (next == _desired) return;
    _desired = next;
    if (next == null) {
      _enqueue(_detach);
    } else if (!_paused) {
      _enqueue(() => _bind(next));
    }
  }

  /// The page is not on screen any more.
  ///
  /// The platform keeps the texture, but nothing stays bound to it and the UI
  /// isolate does not wait for the core to finish with the Surface.
  void pause() {
    if (_paused || _closing) return;
    _paused = true;
    _enqueue(_detach);
  }

  /// The page is on screen again.
  ///
  /// Binding the same texture again is what starts a new decoder generation,
  /// so the core reports the first frame after this like any other first frame.
  void resume() {
    if (!_paused) return;
    _paused = false;
    final target = _desired;
    if (target == null) return;
    _enqueue(() => _bind(target));
  }

  /// The core reports that [evt]'s display now really gets its frames from the
  /// registered Surface, or that it fell back to memory frames.
  ///
  /// The report is sent for every output mode and Surface generation, so each
  /// one describes the current state and not only a change. It may also arrive
  /// while [showTarget]'s registration is still in flight, which is why the
  /// display being bound counts as well.
  Future<void> handleOutputEvent(Map<String, dynamic> evt) async {
    if (_closing || _paused) return;
    final display = int.tryParse('${evt['display'] ?? ''}');
    if (display == null) return;
    if ((_bound?.display ?? _binding) != display) {
      // A report about a registration this page already replaced.
      return;
    }
    final surface = evt['surface']?.toString() == 'true';
    if (_surfaceOutput != surface) {
      _surfaceOutput = surface;
      notifyListeners();
    }
    if (surface) await onSurfaceFrame();
  }

  /// Ends this page's use of the texture.
  ///
  /// It resolves once the core has confirmed that its Surface decoders stopped
  /// and the texture is destroyed. The session is closed after this, never
  /// before, or a decoder can paint into a released texture.
  Future<void> close() => _closeFuture ??= _close();

  /// A new session starts on the same owner.
  ///
  /// The FFI owner calls this only after the previous close future completes.
  void reset() {
    if (!_closing) return;
    _closing = false;
    _paused = false;
    _detachFailed = false;
    _surfaceOutput = false;
    _binding = null;
    _desired = null;
    _closeFuture = null;
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      pause();
    } else if (state == AppLifecycleState.resumed) {
      resume();
    }
  }

  Future<void> _close() async {
    _closing = true;
    WidgetsBinding.instance.removeObserver(this);
    _desired = null;
    _binding = null;
    _setSurfaceOutput(false);
    await _enqueue(_detach);
    await _enqueue(_disposeTexture);
  }

  bool get _usable => !_closing && !_paused && !_detachFailed;

  Future<void> _bind(OhosVideoTarget target) async {
    if (!_usable) return;
    if (_bound != null) {
      // Another display or size means another decoder generation, so the old
      // registration is dropped first: no decoder may keep painting into what
      // is being replaced.
      await _detach();
      if (!_usable) return;
    }
    if (_textureId < 0 && !await _createTexture(target)) return;
    if (!_usable) return;
    final id = _textureId;
    // A fresh registration has painted nothing yet, and the core can report a
    // frame of this generation before this call returns, so the mode is reset
    // for the display being bound instead of after the answer.
    _binding = target.display;
    _setSurfaceOutput(false);
    try {
      await platformChannel.invokeMethod<Object>(
        'registerVideoTexture',
        {
          'sessionId': target.sessionId,
          'display': target.display,
          'textureId': id,
          'width': target.width,
          'height': target.height,
        },
      );
    } catch (error) {
      _binding = null;
      _setSurfaceOutput(false);
      debugPrint('OHOS video texture $id: registering $target failed: $error');
      return;
    }
    _binding = null;
    _bound = target;
    debugPrint('OHOS video texture $id: bound to $target');
  }

  Future<bool> _createTexture(OhosVideoTarget target) async {
    try {
      final created = await platformChannel.invokeMethod<Object>(
        'createVideoTexture',
        {'width': target.width, 'height': target.height},
      );
      final id = created is Map ? created['textureId'] : null;
      if (id is! int || id < 0) {
        debugPrint('OHOS video texture: createVideoTexture returned $created');
        return false;
      }
      _textureId = id;
      notifyListeners();
      debugPrint(
          'OHOS video texture $id: created for ${target.width}x${target.height}');
      return true;
    } catch (error) {
      // The memory picture keeps painting and a later target can try again.
      debugPrint('OHOS video texture: createVideoTexture failed: $error');
      return false;
    }
  }

  Future<void> _detach() async {
    final bound = _bound;
    _setSurfaceOutput(false);
    if (bound == null) return;
    try {
      await platformChannel.invokeMethod<Object>(
        'detachVideoSurfaces',
        {'sessionId': bound.sessionId},
      );
      _bound = null;
      _detachFailed = false;
    } catch (error) {
      // The core did not confirm that it left the Surface, so the texture must
      // not be destroyed and nothing new may be bound into it.
      _detachFailed = true;
      debugPrint(
          'OHOS video texture: detaching ${bound.sessionId} failed: $error');
      rethrow;
    }
  }

  Future<void> _disposeTexture() async {
    final id = _textureId;
    if (id < 0) return;
    if (_detachFailed) {
      throw StateError('OHOS video texture $id is still bound');
    }
    try {
      await platformChannel.invokeMethod<Object>(
        'disposeVideoTexture',
        {'textureId': id},
      );
      _textureId = -1;
      notifyListeners();
      debugPrint('OHOS video texture $id: destroyed');
    } catch (error) {
      debugPrint('OHOS video texture $id: destroying failed: $error');
      rethrow;
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  void _setSurfaceOutput(bool surface) {
    if (_surfaceOutput == surface) return;
    _surfaceOutput = surface;
    notifyListeners();
  }

  /// Runs [action] after everything queued before it, so binding, unbinding and
  /// destroying can never interleave. A failed action does not stop the queue.
  Future<void> _enqueue(Future<void> Function() action) {
    final next = _tail.then((_) => action());
    _tail = next.then<void>((_) {}, onError: (Object _, StackTrace __) {});
    return next;
  }
}
