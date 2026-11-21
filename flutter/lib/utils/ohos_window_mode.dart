import 'dart:math' as math;

/// Classifies the HarmonyOS window into desktop freeform vs. mobile mini-window.
///
/// - Phone (fullscreen, split-screen, or floating mini-window) always stays on
///   the mobile layout; a floating phone window is a mini-window.
/// - Tablet in normal floating mini-window (`isInFreeWindowMode == false`) stays
///   on the mobile layout as a mini-window.
/// - Tablet in free-multi-window or PC mode (`isInFreeWindowMode == true`) uses
///   the desktop layout (even when maximized or fullscreen within that mode).
/// - PC / 2in1 devices always use the desktop layout.
({bool freeform, bool miniWindow}) classifyOhosWindowMode({
  required String deviceType,
  required String? status,
  required bool? isInFreeWindowMode,
}) {
  final isPc = deviceType == '2in1';
  final freeform = !isPc && deviceType != 'phone' && isInFreeWindowMode == true;
  final miniWindow = !isPc && !freeform && status == 'floating';
  return (freeform: freeform, miniWindow: miniWindow);
}

/// Rescales and recenters a mobile remote canvas when the viewport changes
/// (entering/leaving mini-window or rotating portrait/landscape) while keeping
/// the user's relative zoom ratio and focal point.
({double x, double y, double scale})? rescaleCanvasForViewport({
  required double oldWidth,
  required double oldHeight,
  required double newWidth,
  required double newHeight,
  required double displayWidth,
  required double displayHeight,
  required double oldScale,
  required double oldX,
  required double oldY,
}) {
  if (oldWidth <= 0 ||
      oldHeight <= 0 ||
      newWidth <= 0 ||
      newHeight <= 0 ||
      displayWidth <= 0 ||
      displayHeight <= 0 ||
      oldScale <= 0) {
    return null;
  }
  final oldFit = math.min(oldWidth / displayWidth, oldHeight / displayHeight);
  final newFit = math.min(newWidth / displayWidth, newHeight / displayHeight);
  if (oldFit <= 0 || newFit <= 0) {
    return null;
  }
  final zoomRatio = oldScale / oldFit;
  final newScale = newFit * zoomRatio;
  final centerRemoteX = (oldWidth / 2 - oldX) / oldScale;
  final centerRemoteY = (oldHeight / 2 - oldY) / oldScale;

  final scaledW = displayWidth * newScale;
  final scaledH = displayHeight * newScale;
  final newX = scaledW <= newWidth
      ? (newWidth - scaledW) / 2
      : (newWidth / 2 - centerRemoteX * newScale)
          .clamp(newWidth - scaledW, 0.0)
          .toDouble();
  final newY = scaledH <= newHeight
      ? (newHeight - scaledH) / 2
      : (newHeight / 2 - centerRemoteY * newScale)
          .clamp(newHeight - scaledH, 0.0)
          .toDouble();
  return (x: newX, y: newY, scale: newScale);
}
