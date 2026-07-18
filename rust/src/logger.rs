//! 날짜별 파일 로거 (logs/년/월/일.log) + stderr 출력

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

pub struct Logger {
    inner: Mutex<()>,
}

static LOGGER: Logger = Logger {
    inner: Mutex::new(()),
};

fn log_path(now: &chrono::DateTime<chrono::Local>) -> PathBuf {
    PathBuf::from("logs")
        .join(now.format("%Y").to_string())
        .join(now.format("%m").to_string())
        .join(format!("{}.log", now.format("%d")))
}

fn write(level: &str, msg: &str) {
    let now = chrono::Local::now();
    let line = format!("{} - {} - {}\n", now.format("%Y-%m-%d %H:%M:%S"), level, msg);
    let _guard = LOGGER.inner.lock().unwrap();
    eprint!("{}", line);
    let path = log_path(&now);
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
    }
}

pub fn info(msg: &str) {
    write("INFO", msg);
}

pub fn warn(msg: &str) {
    write("WARNING", msg);
}

pub fn error(msg: &str) {
    write("ERROR", msg);
}
