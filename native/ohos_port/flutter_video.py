"""Flutter wiring for the author's existing OHOS Surface renderer.

No changes to libs/scrap, codec selection, conversion, queues or FPS policy.
The completion barrier is needed because Flutter owns the texture consumer:
returning after merely enqueueing ResetDecoder is too early to release it.
"""


def patch_flutter(text, replace):
    text = replace(text, '    use_texture_render: Arc<AtomicBool>,\n', '''    use_texture_render: Arc<AtomicBool>,
    #[cfg(target_env = "ohos")]
    ohos_render_id: SessionID,
    #[cfg(target_env = "ohos")]
    ohos_video_output: Arc<std::sync::Mutex<HashMap<usize, (bool, Option<(u64, u64)>)>>>,
''')
    text = replace(text, '            session_handlers: Default::default(),\n', '''            session_handlers: Default::default(),
            #[cfg(target_env = "ohos")]
            ohos_render_id: SessionID::new_v4(),
            #[cfg(target_env = "ohos")]
            ohos_video_output: Default::default(),
''')
    text = replace(text, 'impl InvokeUiSession for FlutterHandler {\n', '''impl InvokeUiSession for FlutterHandler {
    #[cfg(target_env = "ohos")]
    fn ohos_session_id(&self) -> SessionID {
        // One stable render identity for the core session, independent of the
        // ordering/lifetime of the Flutter UI session handles attached to it.
        self.ohos_render_id
    }

    #[cfg(target_env = "ohos")]
    fn on_ohos_video_output(&self, display: usize, surface: bool) {
        let output = (surface, scrap::ohos::direct_render::current_surface());
        if self.ohos_video_output.lock().unwrap().insert(display, output) == Some(output) {
            return;
        }
        self.push_event(
            "ohos_video_output",
            &[("display", display.to_string()), ("surface", surface.to_string())],
            &[],
        );
    }

''')
    return replace(text, 'pub type FlutterSession = Arc<Session<FlutterHandler>>;\n', '''pub type FlutterSession = Arc<Session<FlutterHandler>>;

#[cfg(target_env = "ohos")]
lazy_static::lazy_static! {
    // Retain a registered session until its decoder completion barrier finishes,
    // even if a concurrent session close removes its UI lookup first.
    static ref OHOS_VIDEO_SESSIONS: std::sync::Mutex<HashMap<SessionID, FlutterSession>> = Default::default();
}

#[cfg(target_env = "ohos")]
pub(crate) fn ohos_init_video(
    id: SessionID, display: usize, surface_id: u64, width: i32, height: i32,
) -> Option<c_int> {
    let session = sessions::get_session_by_session_id(&id)?;
    OHOS_VIDEO_SESSIONS.lock().unwrap().insert(id, session.clone());
    let generation = scrap::ohos::direct_render::register(
        session.ohos_session_id().as_u128(), display, surface_id, width, height,
    );
    session.send(Data::ResetDecoder(Some(display)));
    log::info!("Flutter video surface registered: display={display}, surface={surface_id}, generation={generation}, size={width}x{height}");
    Some(0)
}

#[cfg(target_env = "ohos")]
pub(crate) fn ohos_deinit_video(id: SessionID) -> Option<c_int> {
    let session = OHOS_VIDEO_SESSIONS.lock().unwrap().get(&id).cloned()?;
    scrap::ohos::direct_render::unregister(session.ohos_session_id().as_u128());
    let (done, receiver) = std::sync::mpsc::channel();
    let sender = session.sender.read().unwrap().clone();
    let submitted = sender.map_or(false, |sender| sender.send(Data::OhosVideoBarrier(done)).is_ok());
    let barriers = if submitted { receiver.recv().ok() } else { None };
    match barriers {
        Some(Ok(barriers)) => {
            for barrier in barriers {
                if barrier.recv().is_err() {
                    log::error!("Flutter video detach: decoder did not acknowledge reset");
                    return Some(-1);
                }
            }
        }
        Some(Err(())) => {
            log::error!("Flutter video detach: decoder reset could not be submitted");
            return Some(-1);
        }
        None => {
            // The connection loop has gone away. Its video senders disconnect;
            // wait for those workers' decoder destructors, not just IO exit.
            let (count, stopped) = &*session.ohos_video_lifetime;
            let mut count = count.lock().unwrap();
            while *count != 0 {
                count = stopped.wait(count).unwrap();
            }
        }
    }
    OHOS_VIDEO_SESSIONS.lock().unwrap().remove(&id);
    log::info!("Flutter video surface detached: decoder reset/exit completed");
    Some(0)
}
''')


def patch_ohos_ffi(text, replace):
    text = replace(text, '''    if display < 0 || surface_id == 0 || width <= 0 || height <= 0
        || !SESSIONS.read().unwrap().contains_key(&session_id)
    {
        return -1;
    }
''', '''    if display < 0 || surface_id == 0 || width <= 0 || height <= 0 {
        return -1;
    }
    #[cfg(feature = "flutter")]
    if let Some(result) = crate::flutter::ohos_init_video(
        session_id, display as usize, surface_id, width, height,
    ) {
        return result;
    }
    if !SESSIONS.read().unwrap().contains_key(&session_id) {
        return -1;
    }
''')
    return replace(text, '''pub extern "C" fn ohos_deinit_video(session_id: *const c_char) -> c_int {
    let Some(session_id) = parse_session_id(session_id) else {
        return -1;
    };
''', '''pub extern "C" fn ohos_deinit_video(session_id: *const c_char) -> c_int {
    let Some(session_id) = parse_session_id(session_id) else {
        return -1;
    };
    // Flutter's platform bridge invokes this on a worker, then releases the
    // texture only after all old Surface decoders have finished resetting.
    #[cfg(feature = "flutter")]
    if let Some(result) = crate::flutter::ohos_deinit_video(session_id) {
        return result;
    }
''')


def patch_ui_session_interface(text, replace):
    text = replace(text, '    pub thread: Arc<Mutex<Option<std::thread::JoinHandle<()>>>>,\n', '''    pub thread: Arc<Mutex<Option<std::thread::JoinHandle<()>>>>,
    #[cfg(all(target_env = "ohos", feature = "flutter"))]
    pub ohos_video_lifetime: Arc<(Mutex<usize>, std::sync::Condvar)>,
''')
    return replace(text, '''    fn ohos_session_id(&self) -> SessionID {
        Default::default()
    }
''', '''    fn ohos_session_id(&self) -> SessionID {
        Default::default()
    }
    #[cfg(all(target_env = "ohos", feature = "flutter"))]
    fn on_ohos_video_output(&self, _display: usize, _surface: bool) {}
''')


def patch_client(text, replace):
    text = replace(text, '    ResetDecoder(Option<usize>),\n', '''    ResetDecoder(Option<usize>),
    #[cfg(all(target_env = "ohos", feature = "flutter"))]
    OhosVideoBarrier(mpsc::Sender<Result<Vec<mpsc::Receiver<()>>, ()>>),
''')
    text = replace(text, '    Reset,\n', '''    Reset,
    #[cfg(all(target_env = "ohos", feature = "flutter"))]
    OhosReset(mpsc::Sender<()>),
''')
    text = replace(text, 'pub type MediaSender = mpsc::Sender<MediaData>;\n', '''pub type MediaSender = mpsc::Sender<MediaData>;

#[cfg(all(target_env = "ohos", feature = "flutter"))]
struct OhosVideoLifetime(Arc<(Mutex<usize>, std::sync::Condvar)>);

#[cfg(all(target_env = "ohos", feature = "flutter"))]
impl OhosVideoLifetime {
    fn new(state: Arc<(Mutex<usize>, std::sync::Condvar)>) -> Self {
        *state.0.lock().unwrap() += 1;
        Self(state)
    }
}

#[cfg(all(target_env = "ohos", feature = "flutter"))]
impl Drop for OhosVideoLifetime {
    fn drop(&mut self) {
        let (count, stopped) = &*self.0;
        *count.lock().unwrap() -= 1;
        stopped.notify_all();
    }
}
''')
    text = replace(text, '''    let is_view_camera = session.is_view_camera();

    std::thread::spawn(move || {
        #[cfg(windows)]
''', '''    let is_view_camera = session.is_view_camera();
    #[cfg(all(target_env = "ohos", feature = "flutter"))]
    let video_lifetime = OhosVideoLifetime::new(session.ohos_video_lifetime.clone());

    std::thread::spawn(move || {
        // Declared before video_handler: its Drop runs after the decoder's Drop.
        #[cfg(all(target_env = "ohos", feature = "flutter"))]
        let _video_lifetime = video_lifetime;
        #[cfg(windows)]
''')
    return replace(text, '''                    MediaData::Reset => {
                        if let Some(handler) = video_handler.as_mut() {
                            handler.reset(None);
                        }
                    }
''', '''                    MediaData::Reset => {
                        if let Some(handler) = video_handler.as_mut() {
                            handler.reset(None);
                        }
                    }
                    #[cfg(all(target_env = "ohos", feature = "flutter"))]
                    MediaData::OhosReset(done) => {
                        if let Some(handler) = video_handler.as_mut() {
                            handler.reset(None);
                        }
                        // Reset replaces the decoder and runs the old decoder's
                        // cleanup before acknowledging Flutter's texture owner.
                        let _ = done.send(());
                    }
''')


def patch_client_io_loop(text, replace):
    text = replace(text, '''                *frame_count.write().unwrap() += 1;
                if pixelbuffer {
''', '''                *frame_count.write().unwrap() += 1;
                #[cfg(all(target_env = "ohos", feature = "flutter"))]
                handler.on_ohos_video_output(display, !pixelbuffer);
                if pixelbuffer {
''')
    return replace(text, '''            Data::ResetDecoder(display) => match display {
''', '''            #[cfg(all(target_env = "ohos", feature = "flutter"))]
            Data::OhosVideoBarrier(done) => {
                let mut barriers = Vec::new();
                let mut submitted = true;
                for video in self.video_threads.values() {
                    let (sender, receiver) = std::sync::mpsc::channel();
                    if video.video_sender.send(MediaData::OhosReset(sender)).is_err() {
                        submitted = false;
                        break;
                    }
                    barriers.push(receiver);
                }
                // Never wait in the connection loop: the NAPI worker owns the
                // wait, keeping networking and Flutter's UI thread responsive.
                let _ = done.send(if submitted { Ok(barriers) } else { Err(()) });
            }
            Data::ResetDecoder(display) => match display {
''')


PATCHES = {
    'src/flutter.rs': patch_flutter,
    'src/ohos_ffi.rs': patch_ohos_ffi,
    'src/ui_session_interface.rs': patch_ui_session_interface,
    'src/client.rs': patch_client,
    'src/client/io_loop.rs': patch_client_io_loop,
}
