import 'package:flutter_hbb/utils/ohos_window_mode.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('classifyOhosWindowMode', () {
    test('phone floating mini-window stays mobile and marks miniWindow', () {
      expect(
        classifyOhosWindowMode(
          deviceType: 'phone',
          status: 'floating',
          isInFreeWindowMode: false,
        ),
        (freeform: false, miniWindow: true),
      );
      // Even if a ROM reports freeWindowMode=true on a phone, phone never enters desktop UI.
      expect(
        classifyOhosWindowMode(
          deviceType: 'phone',
          status: 'floating',
          isInFreeWindowMode: true,
        ),
        (freeform: false, miniWindow: true),
      );
    });

    test('tablet floating mini-window stays mobile and marks miniWindow', () {
      expect(
        classifyOhosWindowMode(
          deviceType: 'tablet',
          status: 'floating',
          isInFreeWindowMode: false,
        ),
        (freeform: false, miniWindow: true),
      );
    });

    test('tablet free-multi-window / PC mode uses desktop UI', () {
      for (final status in ['floating', 'maximize', 'fullscreen']) {
        expect(
          classifyOhosWindowMode(
            deviceType: 'tablet',
            status: status,
            isInFreeWindowMode: true,
          ),
          (freeform: true, miniWindow: false),
        );
      }
    });

    test('2in1 PC is never classified as miniWindow', () {
      expect(
        classifyOhosWindowMode(
          deviceType: '2in1',
          status: 'floating',
          isInFreeWindowMode: true,
        ),
        (freeform: false, miniWindow: false),
      );
    });
  });

  group('rescaleCanvasForViewport', () {
    test('preserves fit scale when entering portrait mini-window from landscape',
        () {
      // 1920x1080 remote fit in 800x400 landscape -> fit=400/1080, zoom=1.0
      final oldScale = 400.0 / 1080.0;
      final oldX = (800.0 - 1920.0 * oldScale) / 2;
      final next = rescaleCanvasForViewport(
        oldWidth: 800,
        oldHeight: 400,
        newWidth: 240,
        newHeight: 480,
        displayWidth: 1920,
        displayHeight: 1080,
        oldScale: oldScale,
        oldX: oldX,
        oldY: 0,
      )!;
      expect(next.scale, closeTo(240.0 / 1920.0, 1e-6));
      expect(next.x, closeTo(0.0, 1e-6));
      expect(next.y, closeTo((480.0 - 1080.0 * next.scale) / 2, 1e-6));
    });

    test('preserves 2x user zoom ratio across viewport resize', () {
      final oldFit = 400.0 / 1920.0;
      final oldScale = oldFit * 2.0;
      final oldX = (400.0 - 1920.0 * oldScale) / 2;
      final oldY = (800.0 - 1080.0 * oldScale) / 2;
      final next = rescaleCanvasForViewport(
        oldWidth: 400,
        oldHeight: 800,
        newWidth: 200,
        newHeight: 400,
        displayWidth: 1920,
        displayHeight: 1080,
        oldScale: oldScale,
        oldX: oldX,
        oldY: oldY,
      )!;
      final newFit = 200.0 / 1920.0;
      expect(next.scale, closeTo(newFit * 2.0, 1e-6));
    });
  });
}
