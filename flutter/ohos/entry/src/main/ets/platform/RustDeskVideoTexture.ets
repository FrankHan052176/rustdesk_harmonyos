import { MethodCall, MethodResult, SurfaceTextureEntry, TextureRegistry } from '@ohos/flutter_ohos';
import { deinitVideoAndWait, initVideo } from 'libvideo_surface_bridge.so';

/// Result codes of libvideo_surface_bridge.so, see src/main/cpp/napi_init.cpp.
const NATIVE_OK: number = 0;
const NATIVE_INVALID_ARGS: number = 1;
const NATIVE_LIBRARY_UNAVAILABLE: number = 2;
const NATIVE_REJECTED: number = 3;

/// One external texture of this engine and the display the core paints into it.
class VideoTexture {
  readonly textureId: number;
  /// Surface id of the registered texture, kept as the decimal string the core
  /// takes: a u64 surface id must not be rounded through a JS number.
  readonly surfaceId: string;
  /// Session/display currently registered in the core. An empty session means
  /// the texture is detached but deliberately kept so a resume can rebind it.
  sessionId: string = '';
  display: number = -1;
  width: number = 0;
  height: number = 0;
  disposed: boolean = false;

  constructor(textureId: number, surfaceId: string) {
    this.textureId = textureId;
    this.surfaceId = surfaceId;
  }
}

/// The single answer of one channel call.
///
/// Registering, detaching and disposing answer after several awaits, and each
/// one of them can also fail from the queued chain. Recording the answer here
/// makes a second envelope impossible and keeps a failure from being sent after
/// a success that was already reported.
class CallResult {
  private readonly result: MethodResult;
  private answered: boolean = false;

  constructor(result: MethodResult) {
    this.result = result;
  }

  success(value: Object | null): void {
    if (this.answered) {
      return;
    }
    this.answered = true;
    this.result.success(value);
  }

  error(code: string, message: string): void {
    if (this.answered) {
      return;
    }
    this.answered = true;
    this.result.error(code, message, null);
  }
}

/// Reads a channel argument, which StandardMessageCodec decodes into a `Map`.
function readText(call: MethodCall, key: string): string {
  const value: Object | undefined | null = call.argument(key) as Object | undefined | null;
  if (typeof value === 'string') {
    return value as string;
  }
  return '';
}

/// Reads a channel integer argument, falling back to [fallback] when the value
/// is missing or not an integer, so a malformed call fails the same range check
/// as an out-of-range one.
function readInteger(call: MethodCall, key: string, fallback: number): number {
  const value: Object | undefined | null = call.argument(key) as Object | undefined | null;
  if (typeof value !== 'number') {
    return fallback;
  }
  const number = value as number;
  return Number.isInteger(number) ? number : fallback;
}

function nativeMessage(code: number): string {
  if (code === NATIVE_INVALID_ARGS) {
    return 'invalid session/surface/size arguments';
  }
  if (code === NATIVE_LIBRARY_UNAVAILABLE) {
    return 'liblibrustdesk.so or its surface entry points are unavailable';
  }
  if (code === NATIVE_REJECTED) {
    return 'the core rejected the surface registration or did not confirm the release';
  }
  return `native video bridge returned ${code}`;
}

/// The external video textures of exactly one Flutter engine.
///
/// Flutter texture ids are per engine, so one manager owns the textures of one
/// plugin binding and only that engine may unregister them. The core keys
/// surface registrations by session uuid and its `ohos_deinit_video` drops every
/// display of that session, so every state change of this engine is serialised
/// on one chain: a teardown that is still running must never overlap the next
/// registration or the unregister of the surface it is releasing.
export class RustDeskVideoTextures {
  private readonly registry: TextureRegistry;
  private readonly textures: Map<number, VideoTexture> = new Map();
  /// Session teardowns that are still running. A second request for the same
  /// session joins the pending core call instead of starting another one.
  private readonly releases: Map<string, Promise<number>> = new Map();
  private queue: Promise<void> = Promise.resolve();
  private disposed: boolean = false;

  constructor(registry: TextureRegistry) {
    this.registry = registry;
  }

  /// createVideoTexture({width, height}) -> {textureId}
  create(call: MethodCall, result: MethodResult): void {
    const reply = new CallResult(result);
    const width = readInteger(call, 'width', 0);
    const height = readInteger(call, 'height', 0);
    if (width <= 0 || height <= 0) {
      reply.error('OHOS_VIDEO_TEXTURE_SIZE_INVALID',
        'createVideoTexture needs a positive integer width and height');
      return;
    }
    if (this.disposed) {
      reply.error('OHOS_VIDEO_TEXTURE_DISPOSED', 'this Flutter engine is detaching');
      return;
    }
    const textureId = this.registry.getTextureId();
    if (!Number.isSafeInteger(textureId) || textureId <= 0) {
      reply.error('OHOS_VIDEO_TEXTURE_ID_INVALID', `the engine returned texture id ${textureId}`);
      return;
    }
    let surfaceId = 0;
    try {
      // `createSurfaceTexture` answers an entry that was never registered with
      // the engine here, so the id is allocated first and then registered: only
      // that call yields a surface the core can be told to paint into.
      const entry: SurfaceTextureEntry = this.registry.registerTexture(textureId);
      surfaceId = entry.getSurfaceId();
      if (!Number.isSafeInteger(surfaceId) || surfaceId <= 0) {
        throw new Error(`the engine returned surface id ${surfaceId}`);
      }
      this.registry.setTextureBufferSize(textureId, width, height);
    } catch (error) {
      // Nothing was registered for this texture yet, so nothing can be painting
      // into it and the surface is safe to hand back.
      this.unregister(textureId);
      reply.error('OHOS_VIDEO_TEXTURE_CREATE_FAILED', `${error}`);
      return;
    }
    const texture = new VideoTexture(textureId, surfaceId.toString());
    texture.width = width;
    texture.height = height;
    this.textures.set(textureId, texture);
    console.info(`RustDesk video texture created: texture=${textureId} ` +
      `surface=${texture.surfaceId} size=${width}x${height}`);
    const answer = new Map<string, Object>();
    answer.set('textureId', textureId);
    reply.success(answer);
  }

  /// registerVideoTexture({sessionId, display, textureId, width, height}) -> true
  register(call: MethodCall, result: MethodResult): void {
    const reply = new CallResult(result);
    const sessionId = readText(call, 'sessionId');
    const display = readInteger(call, 'display', -1);
    const textureId = readInteger(call, 'textureId', -1);
    const width = readInteger(call, 'width', 0);
    const height = readInteger(call, 'height', 0);
    if (sessionId.length === 0 || display < 0 || textureId < 0 || width <= 0 || height <= 0) {
      reply.error('OHOS_VIDEO_TARGET_INVALID',
        'registerVideoTexture needs sessionId, display, textureId and a positive size');
      return;
    }
    const texture = this.textures.get(textureId);
    if (texture === undefined) {
      reply.error('OHOS_VIDEO_TEXTURE_UNKNOWN',
        `texture ${textureId} was not created by this Flutter engine`);
      return;
    }
    this.enqueue(reply, 'OHOS_VIDEO_REGISTER_FAILED', async (): Promise<void> => {
      await this.registerTexture(texture, sessionId, display, width, height, reply);
    });
  }

  /// detachVideoSurfaces({sessionId}) -> true
  detachSession(call: MethodCall, result: MethodResult): void {
    const reply = new CallResult(result);
    const sessionId = readText(call, 'sessionId');
    if (sessionId.length === 0) {
      reply.error('OHOS_VIDEO_SESSION_INVALID', 'detachVideoSurfaces needs a session id');
      return;
    }
    this.enqueue(reply, 'OHOS_VIDEO_DETACH_FAILED', async (): Promise<void> => {
      const code = await this.releaseSession(sessionId, 'detachVideoSurfaces');
      if (code !== NATIVE_OK) {
        reply.error('OHOS_VIDEO_DETACH_FAILED',
          `ohos_deinit_video returned ${code}: ${nativeMessage(code)}`);
        return;
      }
      reply.success(true);
    });
  }

  /// disposeVideoTexture({textureId}) -> true
  disposeTexture(call: MethodCall, result: MethodResult): void {
    const reply = new CallResult(result);
    const textureId = readInteger(call, 'textureId', -1);
    if (textureId < 0) {
      reply.error('OHOS_VIDEO_TEXTURE_ID_INVALID', 'disposeVideoTexture needs a texture id');
      return;
    }
    this.enqueue(reply, 'OHOS_VIDEO_DISPOSE_FAILED', async (): Promise<void> => {
      await this.releaseTexture(textureId, reply);
    });
  }

  /// Engine teardown: release every remaining surface, without waiting.
  ///
  /// The ArkTS thread must not block on the decoder barrier, so the work is
  /// queued behind the operations still in flight and its promise is left to
  /// settle on its own. A texture whose session release cannot be confirmed
  /// stays registered instead of being freed under a decoder that may still
  /// paint into it.
  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.queue = this.queue.then(async (): Promise<void> => {
      // Keep ownership visible to detach/dispose calls already in the queue.
      // Clearing it before they run would falsely acknowledge an unbind.
      const pending: Array<VideoTexture> = [];
      this.textures.forEach((texture: VideoTexture): void => {
        pending.push(texture);
      });
      const sessions: Array<string> = [];
      for (const texture of pending) {
        if (texture.sessionId.length > 0 && !sessions.includes(texture.sessionId)) {
          sessions.push(texture.sessionId);
        }
      }
      const released: Map<string, number> = new Map();
      for (const sessionId of sessions) {
        released.set(sessionId, await this.teardownSession(sessionId, 'engineDetach'));
      }
      for (const texture of pending) {
        if (texture.sessionId.length > 0 && released.get(texture.sessionId) !== NATIVE_OK) {
          console.error(`RustDesk video texture ${texture.textureId} stays registered: ` +
            'its session surface release was not confirmed');
          continue;
        }
        this.unregister(texture.textureId);
        texture.disposed = true;
        this.textures.delete(texture.textureId);
      }
      console.info(`RustDesk video engine teardown: ${pending.length} texture(s), ` +
        `${sessions.length} session(s)`);
    }).catch((error: Object): void => {
      console.error(`RustDesk video engine teardown failed: ${String(error)}`);
    });
  }

  /// Registers one texture for a session/display and size.
  private async registerTexture(texture: VideoTexture, sessionId: string, display: number,
    width: number, height: number, reply: CallResult): Promise<void> {
    if (this.disposed || texture.disposed) {
      reply.error('OHOS_VIDEO_TEXTURE_DISPOSED', 'the texture is being released');
      return;
    }
    if (texture.sessionId.length > 0 && texture.sessionId !== sessionId) {
      // The core's teardown is session-wide, so the previous session has to be
      // released before this texture renders another one; a refused teardown
      // must not be recorded as a completed rebind.
      const code = await this.releaseSession(texture.sessionId, 'targetChanged');
      if (code !== NATIVE_OK) {
        reply.error('OHOS_VIDEO_DETACH_FAILED', `releasing the previous session failed: ` +
          `${nativeMessage(code)} (code ${code})`);
        return;
      }
    }
    if (texture.sessionId === sessionId && texture.display === display &&
      texture.width === width && texture.height === height) {
      // Exactly the registration already in place: report it without rebuilding
      // the decoder a second time.
      reply.success(true);
      return;
    }
    // A new display or size of the same session is a new decoder generation for
    // that display only, so it is registered without tearing down the session:
    // `ohos_deinit_video` is session-wide and would also drop the other
    // displays this session may already render into on this engine.
    this.registry.setTextureBufferSize(texture.textureId, width, height);
    const code = initVideo(sessionId, display, texture.surfaceId, width, height);
    if (code !== NATIVE_OK) {
      console.error(`RustDesk video texture ${texture.textureId} initVideo failed: ` +
        `code=${code} session=${sessionId} display=${display} ` +
        `surface=${texture.surfaceId} size=${width}x${height} ${nativeMessage(code)}`);
      reply.error('OHOS_VIDEO_REGISTER_FAILED',
        `ohos_init_video returned ${code}: ${nativeMessage(code)}`);
      return;
    }
    texture.sessionId = sessionId;
    texture.display = display;
    texture.width = width;
    texture.height = height;
    console.info(`RustDesk video texture ${texture.textureId} initVideo ok: ` +
      `session=${sessionId} display=${display} surface=${texture.surfaceId} ` +
      `size=${width}x${height}`);
    reply.success(true);
  }

  /// Releases the registered target of one texture, then frees the texture.
  private async releaseTexture(textureId: number, reply: CallResult): Promise<void> {
    const texture = this.textures.get(textureId);
    if (texture === undefined) {
      // Disposing twice, or disposing after the engine cleaned up, is not an
      // error the caller can act on.
      console.info(`RustDesk video texture ${textureId} is already released`);
      reply.success(true);
      return;
    }
    if (texture.sessionId.length > 0) {
      const code = await this.releaseSession(texture.sessionId, 'disposeVideoTexture');
      if (code !== NATIVE_OK) {
        // Keep the texture registered: unregistering the surface under a decoder
        // that could not confirm its release would let the core paint into a
        // surface the engine already reclaimed.
        reply.error('OHOS_VIDEO_DETACH_FAILED',
          `ohos_deinit_video returned ${code}: ${nativeMessage(code)}`);
        return;
      }
    }
    this.unregister(textureId);
    texture.disposed = true;
    this.textures.delete(textureId);
    console.info(`RustDesk video texture ${textureId} unregistered`);
    reply.success(true);
  }

  /// Releases [sessionId] in the core and marks the textures that were bound to
  /// it detached. Nothing is marked detached unless the core confirmed the
  /// release, so a failed teardown is reported instead of being recorded as
  /// success, and the textures plus their surfaces are kept for a resume.
  private async releaseSession(sessionId: string, reason: string): Promise<number> {
    const bound = this.texturesOfSession(sessionId);
    if (bound.length === 0) {
      return NATIVE_OK;
    }
    const code = await this.teardownSession(sessionId, reason);
    if (code !== NATIVE_OK) {
      return code;
    }
    for (const texture of bound) {
      texture.sessionId = '';
      texture.display = -1;
      texture.width = 0;
      texture.height = 0;
    }
    console.info(`RustDesk video detach(${reason}) confirmed: session=${sessionId} ` +
      `textures=${bound.length}`);
    return NATIVE_OK;
  }

  /// The core's session-wide teardown, shared by every caller that asks for the
  /// same session while it is still running, so a detach and an engine teardown
  /// cannot queue `ohos_deinit_video` twice for one session.
  private teardownSession(sessionId: string, reason: string): Promise<number> {
    const running = this.releases.get(sessionId);
    if (running !== undefined) {
      console.info(`RustDesk video detach(${reason}) joins the teardown already ` +
        `running: session=${sessionId}`);
      return running;
    }
    const release = this.runTeardown(sessionId, reason);
    this.releases.set(sessionId, release);
    const settle = (): void => {
      if (this.releases.get(sessionId) === release) {
        this.releases.delete(sessionId);
      }
    };
    release.then(settle, settle);
    return release;
  }

  private async runTeardown(sessionId: string, reason: string): Promise<number> {
    let code = NATIVE_LIBRARY_UNAVAILABLE;
    try {
      // The worker returns only after the core's decoder barrier, so the surface
      // is safe to hand back once this resolves. A rejection is a failed bridge,
      // not a completed release.
      code = await deinitVideoAndWait(sessionId);
    } catch (error) {
      console.error(`RustDesk video detach(${reason}) failed: session=${sessionId} ` +
        `${String(error)}`);
      return NATIVE_LIBRARY_UNAVAILABLE;
    }
    if (code !== NATIVE_OK) {
      console.error(`RustDesk video detach(${reason}) failed: code=${code} ` +
        `session=${sessionId} ${nativeMessage(code)}`);
      return code;
    }
    console.info(`RustDesk video detach(${reason}) released: session=${sessionId}`);
    return NATIVE_OK;
  }

  private texturesOfSession(sessionId: string): Array<VideoTexture> {
    const bound: Array<VideoTexture> = [];
    this.textures.forEach((texture: VideoTexture): void => {
      if (texture.sessionId === sessionId) {
        bound.push(texture);
      }
    });
    return bound;
  }

  private unregister(textureId: number): void {
    // Entry.release() is unimplemented in this embedding. Let registry errors
    // reach the channel/engine cleanup handler; an exception is not a release.
    this.registry.unregisterTexture(textureId);
  }

  /// Runs [action] after every operation that came before it. A failure of the
  /// chain answers the call with [errorCode] unless it already answered.
  private enqueue(reply: CallResult, errorCode: string, action: () => Promise<void>): void {
    this.queue = this.queue.then(action).catch((error: Object): void => {
      console.error(`RustDesk video texture operation failed: ${String(error)}`);
      reply.error(errorCode, `${error}`);
    });
  }
}
