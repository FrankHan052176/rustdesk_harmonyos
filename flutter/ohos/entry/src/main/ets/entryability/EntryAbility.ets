
import { FlutterAbility, FlutterEngine } from '@ohos/flutter_ohos';
import { deviceInfo } from '@kit.BasicServicesKit';
import { window } from '@kit.ArkUI';
import { GeneratedPluginRegistrant } from '../plugins/GeneratedPluginRegistrant';
import RustDeskPlatformBridge from '../platform/RustDeskPlatformBridge';

export default class EntryAbility extends FlutterAbility {
  onWindowStageCreate(windowStage: window.WindowStage): void {
    super.onWindowStageCreate(windowStage);
    try {
      const mainWindow = windowStage.getMainWindowSync();
      const freeform = deviceInfo.deviceType !== 'phone' && mainWindow.isInFreeWindowMode();
      if (deviceInfo.deviceType === '2in1' || freeform) {
        mainWindow.setWindowDecorVisible(false);
        mainWindow.setWindowTitleMoveEnabled(false);
      }
    } catch (error) {
      console.error(`Failed to configure the RustDesk Flutter window: ${error}`);
    }
  }

  configureFlutterEngine(flutterEngine: FlutterEngine) {
    super.configureFlutterEngine(flutterEngine)
    GeneratedPluginRegistrant.registerWith(flutterEngine)
    flutterEngine.getPlugins()?.add(new RustDeskPlatformBridge())
  }
}
