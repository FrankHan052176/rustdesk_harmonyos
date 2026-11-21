import { common, Want } from '@kit.AbilityKit';

export class RustDeskSessionLaunch {
  windowId: string;
  payload: string;
  title: string;
  width: number;
  height: number;

  constructor(windowId: string, payload: string, title: string, width: number = 0, height: number = 0) {
    this.windowId = windowId;
    this.payload = payload;
    this.title = title;
    this.width = width;
    this.height = height;
  }
}

export class RustDeskSessionWindows {
  private static nextId: number = 0;
  private static pending: Map<string, RustDeskSessionLaunch> = new Map();

  static async open(
    context: common.UIAbilityContext,
    payload: string,
    title: string,
    width: number = 0,
    height: number = 0,
  ): Promise<string> {
    const windowId = `${Date.now()}-${++RustDeskSessionWindows.nextId}`;
    RustDeskSessionWindows.pending.set(windowId, new RustDeskSessionLaunch(windowId, payload, title, width, height));
    const want: Want = {
      bundleName: context.abilityInfo.bundleName,
      moduleName: context.abilityInfo.moduleName,
      abilityName: 'SessionAbility',
      parameters: { rustdeskSessionWindowId: windowId },
    };
    try {
      await context.startAbility(want);
      return windowId;
    } catch (error) {
      RustDeskSessionWindows.pending.delete(windowId);
      throw new Error(`Unable to start a RustDesk session window: ${error}`);
    }
  }

  static take(windowId: string): RustDeskSessionLaunch | undefined {
    const launch = RustDeskSessionWindows.pending.get(windowId);
    RustDeskSessionWindows.pending.delete(windowId);
    return launch;
  }
}
