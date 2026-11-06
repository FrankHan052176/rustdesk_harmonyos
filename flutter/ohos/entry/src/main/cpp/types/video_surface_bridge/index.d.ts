// NAPI surface of libvideo_surface_bridge.so (entry/src/main/cpp/napi_init.cpp).
//
// The two functions are the Flutter texture side of the controller C ABI in
// docs/harmonyos-migration/ohos-controller-ffi.md. `surfaceId` is a decimal
// string because a surface id is a u64 that must not be rounded through a JS
// number. Every function answers a result code:
//
//   0  the core accepted the call
//   1  invalid session/display/surface/size arguments
//   2  liblibrustdesk.so or its surface entry points are unavailable
//   3  the core rejected the registration or the teardown

export const initVideo: (uuid: string, display: number, surfaceId: string,
                         width: number, height: number) => number;

// Resolves only after the core reports that no decoder can still paint into the
// surface, so the texture may be unregistered afterwards.
export const deinitVideoAndWait: (uuid: string) => Promise<number>;
