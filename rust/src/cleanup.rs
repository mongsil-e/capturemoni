//! 자동 삭제 스레드 - 10분마다 지정된 시간이 지난 이미지 파일 삭제

use crate::logger;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime};

const CLEANUP_INTERVAL_SECS: u64 = 600; // 10분 고정
const IMAGE_EXTS: [&str; 5] = ["jpg", "jpeg", "png", "bmp", "webp"];

pub struct RollingCleanup {
    save_folder: Mutex<PathBuf>,
    cleanup_age_secs: AtomicU64,
    running: AtomicBool,
    stop_signal: Mutex<bool>,
    stop_cv: Condvar,
}

impl RollingCleanup {
    pub fn new(save_folder: PathBuf, cleanup_age_secs: u64) -> Arc<Self> {
        Arc::new(Self {
            save_folder: Mutex::new(save_folder),
            cleanup_age_secs: AtomicU64::new(cleanup_age_secs),
            running: AtomicBool::new(false),
            stop_signal: Mutex::new(false),
            stop_cv: Condvar::new(),
        })
    }

    pub fn set_save_folder(&self, folder: PathBuf) {
        *self.save_folder.lock().unwrap() = folder;
    }

    pub fn update_cleanup_age(&self, secs: u64) {
        self.cleanup_age_secs.store(secs, Ordering::Relaxed);
        logger::info(&format!(
            "자동 삭제 주기가 {:.1}시간으로 업데이트되었습니다.",
            secs as f64 / 3600.0
        ));
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Relaxed)
    }

    pub fn start(self: &Arc<Self>) {
        if self.running.swap(true, Ordering::SeqCst) {
            return;
        }
        *self.stop_signal.lock().unwrap() = false;
        let this = Arc::clone(self);
        std::thread::spawn(move || this.worker());
        logger::info(&format!(
            "자동 삭제 스레드 시작됨 (10분마다 {:.1}시간이 지난 파일 삭제)",
            self.cleanup_age_secs.load(Ordering::Relaxed) as f64 / 3600.0
        ));
    }

    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        *self.stop_signal.lock().unwrap() = true;
        self.stop_cv.notify_all();
        logger::info("자동 삭제 스레드 중지됨");
    }

    /// stop 신호가 오면 true 반환, 타임아웃이면 false
    fn wait_stop(&self, dur: Duration) -> bool {
        let guard = self.stop_signal.lock().unwrap();
        let (guard, _timeout) = self
            .stop_cv
            .wait_timeout_while(guard, dur, |stopped| !*stopped)
            .unwrap();
        *guard
    }

    fn worker(&self) {
        while self.running.load(Ordering::Relaxed) {
            if self.wait_stop(Duration::from_secs(CLEANUP_INTERVAL_SECS)) {
                break;
            }
            if !self.running.load(Ordering::Relaxed) {
                break;
            }
            self.perform_cleanup();
        }
    }

    fn perform_cleanup(&self) {
        let age_secs = self.cleanup_age_secs.load(Ordering::Relaxed);
        let folder = self.save_folder.lock().unwrap().clone();
        logger::info(&format!(
            "자동 삭제 시작 - {:.1}시간이 지난 파일을 스캔하여 삭제합니다...",
            age_secs as f64 / 3600.0
        ));

        let cutoff = SystemTime::now() - Duration::from_secs(age_secs);
        let entries = match std::fs::read_dir(&folder) {
            Ok(e) => e,
            Err(e) => {
                logger::error(&format!("폴더 스캔 실패: {}", e));
                return;
            }
        };

        let mut deleted = 0u32;
        let mut failed = 0u32;
        for entry in entries.flatten() {
            if !self.running.load(Ordering::Relaxed) {
                break;
            }
            let path = entry.path();
            let is_image = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| IMAGE_EXTS.contains(&e.to_lowercase().as_str()))
                .unwrap_or(false);
            if !is_image {
                continue;
            }
            let mtime = match entry.metadata().and_then(|m| m.modified()) {
                Ok(t) => t,
                Err(e) => {
                    logger::warn(&format!("파일 처리 중 오류 ({:?}): {}", path.file_name(), e));
                    failed += 1;
                    continue;
                }
            };
            if mtime < cutoff {
                if self.safe_delete(&path) {
                    deleted += 1;
                } else {
                    failed += 1;
                    logger::warn(&format!("삭제 실패: {:?}", path.file_name()));
                }
            }
        }

        if deleted > 0 || failed > 0 {
            logger::info(&format!(
                "자동 삭제 완료: 삭제 {}개, 실패 {}개",
                deleted, failed
            ));
        }
    }

    fn safe_delete(&self, path: &Path) -> bool {
        for attempt in 0..3 {
            if !self.running.load(Ordering::Relaxed) {
                return false;
            }
            match std::fs::remove_file(path) {
                Ok(_) => return true,
                Err(_) if attempt < 2 => {
                    if self.wait_stop(Duration::from_secs(1)) {
                        return false;
                    }
                }
                Err(_) => return false,
            }
        }
        false
    }
}
