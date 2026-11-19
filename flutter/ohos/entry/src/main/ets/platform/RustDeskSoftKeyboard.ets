import { window } from '@kit.ArkUI';
import { inputMethod } from '@kit.IMEKit';
import { Any, BinaryMessenger, EventChannel, EventSink, StreamHandler } from '@ohos/flutter_ohos';

/// The channel the flutter_keyboard_visibility plugin listens on. The plugin
/// ships an Android and an iOS side only, so without this one the Dart side
/// never learns that the HarmonyOS keyboard opened or closed.
const KEYBOARD_VISIBILITY_CHANNEL_NAME = 'flutter_keyboard_visibility';

/// Reports the docked soft keyboard of one window in the plugin's own format:
/// 1 when it becomes visible, 0 when it is gone. A floating keyboard and the
/// candidate bar of a hardware keyboard occupy no window area and stay 0.
export class RustDeskKeyboardVisibility implements StreamHandler {
  private readonly mainWindow: () => window.Window;
  private events: EventSink | null = null;
  private observed: window.Window | null = null;
  private visible: boolean = false;
  private readonly onKeyboardHeight = (height: number): void => {
    const visible = height > 0;
    if (visible === this.visible) {
      return;
    }
    this.visible = visible;
    this.events?.success(visible ? 1 : 0);
  };

  /// [mainWindow] throws while no UIAbility is attached.
  constructor(messenger: BinaryMessenger, mainWindow: () => window.Window) {
    this.mainWindow = mainWindow;
    new EventChannel(messenger, KEYBOARD_VISIBILITY_CHANNEL_NAME).setStreamHandler(this);
  }

  onListen(args: Any, events: EventSink): void {
    this.stop();
    // Nothing may escape from here: the channel's own error path calls a
    // method JS errors do not have, so its handler would throw in turn.
    try {
      const observed = this.mainWindow();
      observed.on('keyboardHeightChange', this.onKeyboardHeight);
      this.observed = observed;
      this.events = events;
      this.onKeyboardHeight(
        observed.getWindowAvoidArea(window.AvoidAreaType.TYPE_KEYBOARD).bottomRect.height);
    } catch (error) {
      console.error(`RustDesk keyboard listener failed: ${String(error)}`);
    }
  }

  onCancel(args: Any): void {
    this.stop();
  }

  stop(): void {
    const observed = this.observed;
    this.observed = null;
    this.events = null;
    this.visible = false;
    if (observed !== null) {
      try {
        observed.off('keyboardHeightChange', this.onKeyboardHeight);
      } catch (error) {
        // The window is already destroyed when the engine detaches after it.
        console.error(`RustDesk keyboard listener cleanup failed: ${String(error)}`);
      }
    }
  }
}

/// Makes the input method send only committed text to the client Flutter has
/// just attached.
///
/// Flutter-OH subscribes to text previews on every attach and hands the
/// composing buffer to Dart as ordinary text, without a composing range, so a
/// remote session cannot tell Pinyin still being composed from text to type.
/// The input method previews only while both preview events have a subscriber:
/// dropping them turns previews off, and it composes in its own candidate bar
/// instead. Attaching again covers an engine attach that has not completed yet;
/// the controller runs attaches in the order they were requested.
export async function disableImePreviewText(): Promise<void> {
  const controller = inputMethod.getController();
  controller.off('setPreviewText');
  controller.off('finishTextPreview');
  // The type and enter key of the session page's hidden text field.
  const config: inputMethod.TextConfig = {
    inputAttribute: {
      textInputType: inputMethod.TextInputType.MULTILINE,
      enterKeyType: inputMethod.EnterKeyType.NEWLINE,
    },
  };
  await controller.attach(true, config);
}
