// RustDesk Flutter texture bridge.
//
// Flutter's TextureRegistry hands out a surface id; the Rust core paints the
// decoded frame into that surface. liblibrustdesk.so is built separately
// (scripts/build-ohos-port-core.sh) and is NOT linked at build time: the two
// surface entry points of docs/harmonyos-migration/ohos-controller-ffi.md are
// resolved with dlopen/dlsym on first use, and a missing library or symbol is
// reported as a result code instead of taking the UI down.
//
// `ohos_init_video` only records the target and sends a decoder reset, so it is
// called straight from the ArkTS thread. `ohos_deinit_video` has to wait until
// the decoder thread, the SDK decoder and its NativeWindow are gone before the
// caller may unregister the Flutter texture, so it is executed on a NAPI worker
// and awaited through a promise: blocking the ArkTS thread there would freeze
// the window while a session is closed or the app goes to background.

#include "napi/native_api.h"
#include "hilog/log.h"

#include <dlfcn.h>

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <string>

#undef LOG_DOMAIN
#undef LOG_TAG
#define LOG_DOMAIN 0x5253
#define LOG_TAG "RustDeskVideoBridge"

namespace {

// Result codes reported to ArkTS; mirrored by the NATIVE_* constants in
// RustDeskVideoTexture.ets.
constexpr int32_t kOk = 0;
constexpr int32_t kInvalidArgs = 1;
constexpr int32_t kLibraryUnavailable = 2;
constexpr int32_t kRejected = 3;

using InitVideoFn = int32_t (*)(const char* uuid, int32_t display, uint64_t surface_id,
                                int32_t width, int32_t height);
using DeinitVideoFn = int32_t (*)(const char* uuid);

struct CoreApi {
    InitVideoFn init_video = nullptr;
    DeinitVideoFn deinit_video = nullptr;
};

std::mutex g_library_mutex;
void* g_library = nullptr;
bool g_load_attempted = false;
std::string g_library_error;
CoreApi g_core;

std::string LibMessage() {
    std::lock_guard<std::mutex> lock(g_library_mutex);
    if (!g_library_error.empty()) {
        return std::string("liblibrustdesk.so is not usable (") + g_library_error + ")";
    }
    return "liblibrustdesk.so is not usable";
}

// Resolves the two surface entry points once. A failed attempt is remembered so
// a broken installation is reported the same way on every later call instead of
// retrying dlopen on each frame.
bool EnsureCore() {
    std::lock_guard<std::mutex> lock(g_library_mutex);
    if (g_load_attempted) {
        return g_core.init_video != nullptr && g_core.deinit_video != nullptr;
    }
    g_load_attempted = true;
    dlerror();
    g_library = dlopen("liblibrustdesk.so", RTLD_NOW | RTLD_LOCAL);
    if (g_library == nullptr) {
        const char* error = dlerror();
        g_library_error = error != nullptr ? error : "dlopen failed";
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "dlopen liblibrustdesk.so failed: %{public}s", g_library_error.c_str());
        return false;
    }
    g_core.init_video = reinterpret_cast<InitVideoFn>(dlsym(g_library, "ohos_init_video"));
    g_core.deinit_video = reinterpret_cast<DeinitVideoFn>(dlsym(g_library, "ohos_deinit_video"));
    if (g_core.init_video == nullptr || g_core.deinit_video == nullptr) {
        g_library_error = "missing ohos_init_video/ohos_deinit_video";
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "liblibrustdesk.so has no surface entry points: %{public}s",
                     g_library_error.c_str());
        return false;
    }
    return true;
}

napi_value MakeInt32(napi_env env, int32_t value) {
    napi_value result = nullptr;
    napi_create_int32(env, value, &result);
    return result;
}

bool GetArgs(napi_env env, napi_callback_info info, size_t expected, napi_value* argv) {
    size_t argc = expected;
    napi_value self = nullptr;
    if (napi_get_cb_info(env, info, &argc, argv, &self, nullptr) != napi_ok) {
        return false;
    }
    return argc == expected;
}

bool GetString(napi_env env, napi_value value, std::string* out) {
    napi_valuetype type = napi_undefined;
    if (napi_typeof(env, value, &type) != napi_ok || type != napi_string) {
        return false;
    }
    size_t length = 0;
    if (napi_get_value_string_utf8(env, value, nullptr, 0, &length) != napi_ok) {
        return false;
    }
    std::string text(length + 1, '\0');
    size_t copied = 0;
    if (napi_get_value_string_utf8(env, value, text.data(), text.size(), &copied) != napi_ok) {
        return false;
    }
    text.resize(copied);
    out->swap(text);
    return true;
}

bool GetInt32(napi_env env, napi_value value, int32_t* out) {
    napi_valuetype type = napi_undefined;
    if (napi_typeof(env, value, &type) != napi_ok || type != napi_number) {
        return false;
    }
    return napi_get_value_int32(env, value, out) == napi_ok;
}

// ArkTS hands the surface id over as a decimal string: a surface id is a u64
// and does not survive a JS number round trip. Only plain digits that fit a u64
// are accepted, so a rounded or truncated id can never address another surface.
bool ParseSurfaceId(const std::string& text, uint64_t* out) {
    if (text.empty()) {
        return false;
    }
    for (const char digit : text) {
        if (digit < '0' || digit > '9') {
            return false;
        }
    }
    errno = 0;
    char* end = nullptr;
    const unsigned long long value = std::strtoull(text.c_str(), &end, 10);
    if (errno == ERANGE || end == nullptr || *end != '\0') {
        return false;
    }
    *out = static_cast<uint64_t>(value);
    return true;
}

napi_value InitVideo(napi_env env, napi_callback_info info) {
    napi_value argv[5] = {nullptr};
    if (!GetArgs(env, info, 5, argv)) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "initVideo: expected 5 arguments");
        return MakeInt32(env, kInvalidArgs);
    }
    std::string uuid;
    std::string surface;
    int32_t display = 0;
    int32_t width = 0;
    int32_t height = 0;
    if (!GetString(env, argv[0], &uuid) || !GetInt32(env, argv[1], &display) ||
        !GetString(env, argv[2], &surface) || !GetInt32(env, argv[3], &width) ||
        !GetInt32(env, argv[4], &height) || uuid.empty() || display < 0 || width <= 0 ||
        height <= 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "initVideo: invalid session/display/surface/size arguments");
        return MakeInt32(env, kInvalidArgs);
    }
    uint64_t surface_id = 0;
    if (!ParseSurfaceId(surface, &surface_id) || surface_id == 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "initVideo: surface %{public}s is not a usable u64 surface id",
                     surface.c_str());
        return MakeInt32(env, kInvalidArgs);
    }
    if (!EnsureCore()) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "initVideo: %{public}s",
                     LibMessage().c_str());
        return MakeInt32(env, kLibraryUnavailable);
    }
    const int32_t rc = g_core.init_video(uuid.c_str(), display, surface_id, width, height);
    if (rc != 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "initVideo rejected: rc=%{public}d uuid=%{public}s display=%{public}d "
                     "surface=%{public}s size=%{public}dx%{public}d",
                     rc, uuid.c_str(), display, surface.c_str(), width, height);
        return MakeInt32(env, kRejected);
    }
    OH_LOG_Print(LOG_APP, LOG_INFO, LOG_DOMAIN, LOG_TAG,
                 "initVideo ok: uuid=%{public}s display=%{public}d surface=%{public}s "
                 "size=%{public}dx%{public}d",
                 uuid.c_str(), display, surface.c_str(), width, height);
    return MakeInt32(env, kOk);
}

struct DeinitTask {
    napi_async_work work = nullptr;
    napi_deferred deferred = nullptr;
    std::string uuid;
    // Set by the worker, read by the completion callback.
    int32_t code = kOk;
};

void ExecuteDeinit(napi_env env, void* data) {
    (void)env;
    DeinitTask* task = static_cast<DeinitTask*>(data);
    if (!EnsureCore()) {
        task->code = kLibraryUnavailable;
        return;
    }
    const int32_t rc = g_core.deinit_video(task->uuid.c_str());
    task->code = rc == 0 ? kOk : kRejected;
    if (rc == 0) {
        OH_LOG_Print(LOG_APP, LOG_INFO, LOG_DOMAIN, LOG_TAG,
                     "deinitVideo released: uuid=%{public}s", task->uuid.c_str());
    } else {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "deinitVideo rejected: rc=%{public}d uuid=%{public}s", rc,
                     task->uuid.c_str());
    }
}

void CompleteDeinit(napi_env env, napi_status status, void* data) {
    DeinitTask* task = static_cast<DeinitTask*>(data);
    if (status == napi_ok) {
        napi_resolve_deferred(env, task->deferred, MakeInt32(env, task->code));
    } else {
        napi_value message = nullptr;
        napi_create_string_utf8(env, "deinitVideo did not run to completion",
                                NAPI_AUTO_LENGTH, &message);
        napi_value error = nullptr;
        napi_create_error(env, nullptr, message, &error);
        napi_reject_deferred(env, task->deferred, error);
    }
    napi_delete_async_work(env, task->work);
    delete task;
}

// deinitVideoAndWait(uuid: string): Promise<number>
napi_value DeinitVideoAndWait(napi_env env, napi_callback_info info) {
    napi_value argv[1] = {nullptr};
    std::string uuid;
    const bool valid = GetArgs(env, info, 1, argv) && GetString(env, argv[0], &uuid) &&
                       !uuid.empty();
    napi_deferred deferred = nullptr;
    napi_value promise = nullptr;
    if (napi_create_promise(env, &deferred, &promise) != napi_ok) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "deinitVideoAndWait: failed to create the result promise");
        return nullptr;
    }
    if (!valid) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "deinitVideoAndWait: invalid uuid");
        napi_resolve_deferred(env, deferred, MakeInt32(env, kInvalidArgs));
        return promise;
    }
    DeinitTask* task = new DeinitTask();
    task->deferred = deferred;
    task->uuid = uuid;
    napi_value resource = nullptr;
    napi_create_string_utf8(env, "RustDeskDeinitVideo", NAPI_AUTO_LENGTH, &resource);
    if (napi_create_async_work(env, nullptr, resource, ExecuteDeinit, CompleteDeinit, task,
                               &task->work) != napi_ok ||
        napi_queue_async_work(env, task->work) != napi_ok) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "deinitVideoAndWait: could not queue the worker request");
        if (task->work != nullptr) {
            napi_delete_async_work(env, task->work);
        }
        delete task;
        napi_resolve_deferred(env, deferred, MakeInt32(env, kLibraryUnavailable));
        return promise;
    }
    return promise;
}

}  // namespace

EXTERN_C_START
static napi_value VideoBridgeInit(napi_env env, napi_value exports) {
    struct Prop {
        const char* name;
        napi_callback fn;
    };
    const Prop props[] = {
        {"initVideo", InitVideo},
        {"deinitVideoAndWait", DeinitVideoAndWait},
    };
    for (const Prop& prop : props) {
        napi_value fn = nullptr;
        if (napi_create_function(env, prop.name, NAPI_AUTO_LENGTH, prop.fn, nullptr, &fn) !=
            napi_ok) {
            return nullptr;
        }
        if (napi_set_named_property(env, exports, prop.name, fn) != napi_ok) {
            return nullptr;
        }
    }
    return exports;
}
EXTERN_C_END

static napi_module g_video_bridge_module = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = VideoBridgeInit,
    .nm_modname = "video_surface_bridge",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterRustDeskVideoBridgeModule(void) {
    napi_module_register(&g_video_bridge_module);
}
