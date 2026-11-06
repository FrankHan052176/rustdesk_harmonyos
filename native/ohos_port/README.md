# Flutter 鸿蒙客户端：作者上游内核接入

源码固定为 [`wanghd114/rustdesk_harmonyos:ohos-port`](https://github.com/wanghd114/rustdesk_harmonyos/tree/ohos-port) 的 `d53391e9b0b87a94c5ee1f8f40252acb25867395`，精确来源见 `source.lock.json`。默认 checkout 为 `target/ohos-port-upstream-d53391e`。

## 维护边界

内核及依赖按作者基线构建，只叠加 Flutter 编译、会话和 Surface 生命周期接线，不延续旧 fork 的解码/QoS 实现。`hbb_common` 必须保持作者 gitlink 对应的干净提交；不修改其配置路径，不引入个人依赖 fork、旧 `rd_engine` 或自研 QUIC/QoS。

`core_patch.py` 处理 OHOS 的 Flutter cfg、必需 trait 方法和进程内 CM 接线，并组合 `flutter_video.py` 的会话/视频桥接。OHOS 同时报告 `target_os="linux"`，需避开作者未在此目标编译的桌面 IPC、X11、PulseAudio、纹理插件等分支，使用既有移动端分支。公共 FRB 签名及 `EventToUI::Cursor` 保持上游，包括摄像头参数；本产品已删除的语音、聊天、摄像头 UI 不恢复。

以下实现不再叠加：

- 自定义 host 状态机、输入授权/注入及扩展文件/图片剪贴板。
- per-UI RGBA、leased texture、子 XComponent / BuilderNode 中间合成。
- decoder、FPS 采样/诊断、翻译表或 locale 解析改写，以及 hbb_common 沙箱路径补丁。
- 给旧 Dart 调用补齐私有 FRB 函数的兼容桩及这些实现的专属测试。

作者的 `libs/scrap`、输入、剪贴板、采集及 QoS 实现保持原样。Flutter 单显示器通过 `TextureRegistry.registerTexture` 获取 Surface ID，经 NAPI 调用作者 `ohos_init_video`，由作者解码器决定 Surface 直出或 buffer 回退。适配层补齐 Flutter 会话查找、稳定 render ID / TLS、输出模式事件；`ohos_deinit_video` 在 NAPI worker 等待视频线程 reset/退出完成，确认后才注销纹理，再关闭会话。后台解绑、恢复重绑，移动端重连先等待旧会话关闭。无 Surface 的画面保留 FRB/RGBA 路径，OHOS 按 RGBA 而非 BGRA 解读。

语言接入仅把 OHOS 从桌面 Linux ARM64 的 CJK 不可用 cfg 中排除；保留作者翻译表与语言选择逻辑，不强制中文。

被控初始化、服务和捕获使用作者现成的 `ohos_init`、`ohos_server_*`、`ohos_capture_*` C ABI。配置目录遵循作者 `ohos_init` 设置的 `HOME` 及原版 hbb_common，不承诺旧 fork 配置迁移或功能等价。

本分支基于作者的 `ohos-port` 提交，根目录 Rust 源码与作者基线保持一致。Flutter 构建在独立 checkout 上应用薄适配，不修改根 Rust 工作区，也不包含旧 fork 的内核或旧 HAR 构建入口。

## 构建

```sh
# 校验固定源码和补丁输入；不写文件
python3 scripts/ohos-port-core.py prepare --check

# 只准备源码
bash scripts/build-ohos-port-core.sh --source-only

# 生成 FRB 绑定并执行 cargo check；不产出可打包的新原生库
bash scripts/build-ohos-port-core.sh --check

# 生成绑定、构建 release 原生库、检查导出和记录配对输出
bash scripts/build-ohos-ffi.sh

# 本地 default 调试包：目录必须包含本 Flutter bundle 的 default 签名配置
RUSTDESK_SIGNING_DIR=/path/to/RustDesk-Flutter-signing bash scripts/build-ohos-flutter-hap.sh
```

签名资料不随仓库提交。本地 `signingConfigs.json` 由 `RUSTDESK_SIGNING_DIR` 指定；CI 的 publish 签名从 Actions secret `OHOS_RELEASE_SIGNING_BASE64` 注入。

需要 OHOS NDK、Rust `aarch64-unknown-linux-ohos` target、Flutter OHOS SDK、FRB codegen 1.80.1，以及可用的 Cargo/Pub 缓存。缓存指向 NAS 时须先挂载，不创建替代缓存。`RUSTDESK_CORE_ROOT` 可指定同一固定提交的独立 checkout；不接受未知源码修改。

Rust/Dart 绑定从同一 `src/flutter_ffi.rs` 生成。`patch-ohos-frb-dart.py` 只适配 FRB 1.80.1 与当前 Dart/ffigen 的类型及 runtime 绑定，不添加旧 OHOS 私有 API。完整构建在 `target/ohos-port-build/core-build.json` 记录提交、构建输入和 native/Dart 哈希；HAP 打包先校验配对。`--check` 成功不能替代完整构建或用于混装旧 `.so`。

HAP 构建临时选择 Flutter-OH 依赖，结束后恢复 manifest/lockfile，不修改 SDK/cache。本地性能测试使用 `OHOS_BUILD_MODE=release OHOS_PRODUCT=default`，沿用 debug 签名。编译、Surface 出图、生命周期和帧率分别取证，不用编译成功或旧版本观测代替真机验证。
