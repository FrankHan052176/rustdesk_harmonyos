// Raw-frame and clipboard channels between the native capture callbacks and
// the Rust services. Same contract as the Android JNI layer
// (`scrap::android::ffi`): the capture side pushes, the service side takes,
// `enable` decides whether pushing costs anything.
use base::message_proto::MultiClipboards;
use lazy_static::lazy_static;
use std::collections::VecDeque;
use std::ops::Not;
use std::sync::Mutex;
use std::time::{Duration, Instant};

lazy_static! {
    static ref VIDEO_RAW: Mutex<FrameRaw> = Mutex::new(FrameRaw::new("video", MAX_VIDEO_FRAME_TIMEOUT));
    static ref AUDIO_RAW_INNER: Mutex<AudioQueue> =
        Mutex::new(AudioQueue::new("audio-inner", MAX_AUDIO_FRAME_TIMEOUT));
    static ref AUDIO_RAW_MIC: Mutex<AudioQueue> =
        Mutex::new(AudioQueue::new("audio-mic", MAX_AUDIO_FRAME_TIMEOUT));
    static ref CLIPBOARDS_HOST: Mutex<Option<MultiClipboards>> = Mutex::new(None);
    static ref CLIPBOARDS_CLIENT: Mutex<Option<MultiClipboards>> = Mutex::new(None);
}

const MAX_VIDEO_FRAME_TIMEOUT: Duration = Duration::from_millis(100);
const MAX_AUDIO_FRAME_TIMEOUT: Duration = Duration::from_millis(1000);
// 240 ms of 48 kHz stereo s16le PCM (4-byte stereo-frame aligned).
const MAX_AUDIO_QUEUE_BYTES: usize = 3840 * 12;
// The capture callback must copy synchronously; skip the copy while no service
// is pulling, otherwise an on-screen capture would memcpy at the display rate
// with nobody consuming.
const UNCONSUMED_SKIP: Duration = Duration::from_millis(300);

struct FrameRaw {
    name: &'static str,
    buf: Vec<u8>,
    fresh: bool,
    last_update: Instant,
    last_taken: Instant,
    timeout: Duration,
    enable: bool,
}

impl FrameRaw {
    fn new(name: &'static str, timeout: Duration) -> Self {
        FrameRaw {
            name,
            buf: Vec::new(),
            fresh: false,
            last_update: Instant::now(),
            last_taken: Instant::now(),
            timeout,
            enable: false,
        }
    }

    fn set_enable(&mut self, value: bool) {
        self.enable = value;
        self.fresh = false;
        self.buf.clear();
    }

    fn update(&mut self, data: &[u8]) {
        if self.enable.not() || self.last_taken.elapsed() > UNCONSUMED_SKIP {
            return;
        }
        self.buf.clear();
        self.buf.extend_from_slice(data);
        self.fresh = true;
        self.last_update = Instant::now();
    }

    fn take<'a>(&mut self, dst: &mut Vec<u8>, last: &mut Vec<u8>) -> Option<()> {
        self.last_taken = Instant::now();
        if self.enable.not() || !self.fresh {
            return None;
        }
        if self.last_update.elapsed() > self.timeout {
            log::trace!("Failed to take {} raw,timeout!", self.name);
            return None;
        }
        self.fresh = false;
        if last.len() == self.buf.len()
            && crate::would_block_if_equal(last, &self.buf).is_err()
        {
            return None;
        }
        dst.resize(self.buf.len(), 0);
        dst.copy_from_slice(&self.buf);
        Some(())
    }
}

struct AudioQueue {
    name: &'static str,
    buf: VecDeque<u8>,
    last_update: Instant,
    last_taken: Instant,
    timeout: Duration,
    enable: bool,
}

impl AudioQueue {
    fn new(name: &'static str, timeout: Duration) -> Self {
        AudioQueue {
            name,
            buf: VecDeque::new(),
            last_update: Instant::now(),
            last_taken: Instant::now(),
            timeout,
            enable: false,
        }
    }

    fn set_enable(&mut self, value: bool) {
        self.enable = value;
        self.buf.clear();
    }

    fn push(&mut self, data: &[u8]) {
        if self.enable.not() || self.last_taken.elapsed() > UNCONSUMED_SKIP || data.is_empty() {
            return;
        }
        if data.len() >= MAX_AUDIO_QUEUE_BYTES {
            self.buf.clear();
            let drop_head = ((data.len() - MAX_AUDIO_QUEUE_BYTES + 3) / 4) * 4;
            if drop_head < data.len() {
                self.buf.extend(&data[drop_head..]);
            }
        } else {
            let total = self.buf.len() + data.len();
            if total > MAX_AUDIO_QUEUE_BYTES {
                let overflow = total - MAX_AUDIO_QUEUE_BYTES;
                let drop_bytes = ((overflow + 3) / 4) * 4;
                let actual_drop = drop_bytes.min(self.buf.len());
                self.buf.drain(..actual_drop);
            }
            self.buf.extend(data);
        }
        self.last_update = Instant::now();
    }

    fn take(&mut self, dst: &mut Vec<u8>) -> Option<()> {
        self.last_taken = Instant::now();
        if self.enable.not() || self.buf.is_empty() {
            return None;
        }
        if self.last_update.elapsed() > self.timeout {
            log::trace!("Failed to take {} raw,timeout!", self.name);
            self.buf.clear();
            return None;
        }
        dst.clear();
        dst.reserve(self.buf.len());
        let (front, back) = self.buf.as_slices();
        dst.extend_from_slice(front);
        dst.extend_from_slice(back);
        self.buf.clear();
        Some(())
    }
}

pub fn set_video_raw_enable(enable: bool) {
    VIDEO_RAW.lock().unwrap().set_enable(enable);
}

pub fn set_audio_raw_enable(enable: bool) {
    AUDIO_RAW_INNER.lock().unwrap().set_enable(enable);
    AUDIO_RAW_MIC.lock().unwrap().set_enable(enable);
}

/// Push a frame from the AVScreenCapture buffer-available callback. The
/// callback copies synchronously; the source buffer is invalid afterwards.
pub fn push_video_frame(data: &[u8]) {
    VIDEO_RAW.lock().unwrap().update(data);
}

/// Push an audio frame (PCM s16le, 48 kHz, channels per capture config) from
/// the capture callback. `mic` selects the microphone stream, else inner
/// (system) audio.
pub fn push_audio_frame(data: &[u8], mic: bool) {
    if mic {
        AUDIO_RAW_MIC.lock().unwrap().push(data);
    } else {
        AUDIO_RAW_INNER.lock().unwrap().push(data);
    }
}

pub fn get_video_raw(dst: &mut Vec<u8>, last: &mut Vec<u8>) -> Option<()> {
    VIDEO_RAW.lock().ok()?.take(dst, last)
}

/// Same as android's single audio channel, except the consumer names the
/// active source: mic while a voice call is active, else system audio.
pub fn get_audio_raw(dst: &mut Vec<u8>, _last: &mut Vec<u8>, mic: bool) -> Option<()> {
    if mic {
        AUDIO_RAW_MIC.lock().ok()?.take(dst)
    } else {
        AUDIO_RAW_INNER.lock().ok()?.take(dst)
    }
}

pub fn get_clipboards(client: bool) -> Option<MultiClipboards> {
    if client {
        CLIPBOARDS_CLIENT.lock().ok()?.take()
    } else {
        CLIPBOARDS_HOST.lock().ok()?.take()
    }
}

/// Store a clipboard snapshot pushed from the ArkTS pasteboard. `client`
/// marks content produced by our own client sessions, `false` is the local
/// (host) clipboard shared with remote peers.
pub fn set_clipboards(clips: MultiClipboards, client: bool) {
    if client {
        *CLIPBOARDS_CLIENT.lock().unwrap() = Some(clips);
    } else {
        *CLIPBOARDS_HOST.lock().unwrap() = Some(clips);
    }
}
