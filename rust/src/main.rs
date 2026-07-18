//! 화면 모니터링 - watch.py의 Rust 마이그레이션 (Windows 7 호환)
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod capture;
mod cleanup;
mod logger;

use cleanup::RollingCleanup;
use eframe::egui;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tray_icon::menu::{Menu, MenuEvent, MenuItem};
use tray_icon::{TrayIcon, TrayIconBuilder};

const CLEANUP_TIMER_SECS: u64 = 600;

#[derive(Clone)]
struct CaptureSettings {
    interval_secs: f64,
    save_folder: PathBuf,
    webp: bool,
    quality: u8,
    resolution: String, // "원본" 또는 "1920x1080" 등
    grayscale: bool,
}

struct Shared {
    capturing: AtomicBool,
    capture_count: AtomicU64,
    status: Mutex<String>,
    settings: Mutex<CaptureSettings>,
    cleanup_enabled: AtomicBool,
    quit: AtomicBool,
}

thread_local! {
    // 트레이 메뉴 항목(시작/정지)은 !Send이므로 메인 스레드 전용 저장소에 둔다.
    // 메뉴 이벤트 핸들러와 egui UI 모두 메인 스레드에서 실행되므로 안전하다.
    static TRAY_ITEMS: std::cell::RefCell<Option<(MenuItem, MenuItem)>> =
        std::cell::RefCell::new(None);
}

/// 캡처 상태에 맞춰 트레이 메뉴 라벨/활성화 상태를 갱신 (메인 스레드에서만 호출)
fn sync_tray_menu(capturing: bool) {
    TRAY_ITEMS.with(|cell| {
        if let Some((start, stop)) = &*cell.borrow() {
            start.set_enabled(!capturing);
            start.set_text(if capturing {
                "⏸ 모니터링 시작 (실행 중)"
            } else {
                "▶ 모니터링 시작"
            });
            stop.set_enabled(capturing);
            stop.set_text(if capturing {
                "⏹ 모니터링 정지"
            } else {
                "⏸ 모니터링 정지 (정지됨)"
            });
        }
    });
}

/// 캡처 시작 (UI 스레드/트레이 핸들러 어디서든 호출 가능)
fn start_capture_shared(
    shared: &Arc<Shared>,
    rolling_cleanup: &Arc<RollingCleanup>,
    ctx: &egui::Context,
) {
    if shared.capturing.swap(true, Ordering::SeqCst) {
        return;
    }
    *shared.status.lock().unwrap() = "캡처 준비 중...".into();
    let shared2 = Arc::clone(shared);
    let ctx2 = ctx.clone();
    std::thread::spawn(move || capture_worker(shared2, ctx2));
    if shared.cleanup_enabled.load(Ordering::Relaxed) {
        rolling_cleanup.start();
    }
    sync_tray_menu(true);
    logger::info("캡처 시작됨");
}

/// 캡처 정지 (UI 스레드/트레이 핸들러 어디서든 호출 가능)
fn stop_capture_shared(shared: &Arc<Shared>, rolling_cleanup: &Arc<RollingCleanup>) {
    if !shared.capturing.swap(false, Ordering::SeqCst) {
        return;
    }
    *shared.status.lock().unwrap() = "캡처 정지됨".into();
    if rolling_cleanup.is_running() {
        rolling_cleanup.stop();
    }
    sync_tray_menu(false);
    logger::info("캡처 정지됨");
}

/// 창 제목으로 메인 창 HWND를 찾는다 (CreationContext에서 핸들을 못 얻은 경우 폴백)
fn find_main_window() -> isize {
    use windows_sys::Win32::UI::WindowsAndMessaging::FindWindowW;
    let title: Vec<u16> = "화면 모니터링\0".encode_utf16().collect();
    unsafe { FindWindowW(std::ptr::null(), title.as_ptr()) }
}

/// 숨겨진 창을 Win32 API로 직접 표시 (숨김 상태에서는 egui 루프가 돌지 않으므로 필요)
fn show_window_raw(hwnd: isize) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetForegroundWindow, ShowWindow, SW_SHOW,
    };
    let hwnd = if hwnd != 0 { hwnd } else { find_main_window() };
    if hwnd == 0 {
        logger::warn("메인 창 HWND를 찾지 못해 창을 표시할 수 없습니다");
        return;
    }
    unsafe {
        ShowWindow(hwnd, SW_SHOW);
        SetForegroundWindow(hwnd);
    }
}

fn parse_resolution(s: &str) -> Option<(u32, u32)> {
    let (w, h) = s.split_once('x')?;
    Some((w.parse().ok()?, h.parse().ok()?))
}

/// 캡처 작업자 스레드
fn capture_worker(shared: Arc<Shared>, ctx: egui::Context) {
    let font = capture::load_font();
    if font.is_none() {
        logger::warn("시스템 폰트를 찾을 수 없어 타임스탬프 오버레이를 생략합니다");
    }
    let mut logged_area = false;
    while shared.capturing.load(Ordering::Relaxed) {
        let settings = shared.settings.lock().unwrap().clone();
        match capture::grab_screen() {
            Ok(mut img) => {
                if !logged_area {
                    logged_area = true;
                    logger::info(&format!(
                        "캡처 영역: {}x{} (모니터 {}개)",
                        img.width(),
                        img.height(),
                        capture::monitor_rects().len()
                    ));
                }
                capture::add_timestamp_overlay(&mut img, font.as_ref());
                let ts = chrono::Local::now().format("%Y%m%d_%H%M%S_%3f").to_string();
                let ext = if settings.webp { "webp" } else { "jpg" };
                let path = settings.save_folder.join(format!("screenshot_{}.{}", ts, ext));
                let opts = capture::SaveOptions {
                    webp: settings.webp,
                    quality: settings.quality,
                    resolution: parse_resolution(&settings.resolution),
                    grayscale: settings.grayscale,
                };
                match capture::save_image(img, &path, &opts) {
                    Ok(_) => {
                        let n = shared.capture_count.fetch_add(1, Ordering::Relaxed) + 1;
                        *shared.status.lock().unwrap() = format!("캡처 중... ({}번째)", n);
                    }
                    Err(e) => {
                        logger::error(&e);
                        *shared.status.lock().unwrap() = format!("저장 오류 - 재시도: {}", e);
                    }
                }
                ctx.request_repaint();
            }
            Err(e) => {
                logger::error(&format!("화면 캡처 실패: {}", e));
                *shared.status.lock().unwrap() = "캡처 에러 - 잠시 후 재시도".into();
                ctx.request_repaint();
                std::thread::sleep(Duration::from_secs(3));
                continue;
            }
        }
        // 설정된 간격만큼 대기 (0.2초 단위로 중지 신호 확인)
        let mut remaining = settings.interval_secs;
        while remaining > 0.0 && shared.capturing.load(Ordering::Relaxed) {
            let step = remaining.min(0.2);
            std::thread::sleep(Duration::from_secs_f64(step));
            remaining -= step;
        }
    }
}

struct TrayMenu {
    _tray: TrayIcon,
    show_item: MenuItem,
    start_item: MenuItem,
    stop_item: MenuItem,
    quit_item: MenuItem,
}

/// 앱 아이콘 (흰 배경 + 검정 '캡처' 텍스트) RGBA 디코딩
fn load_app_icon() -> Result<(Vec<u8>, u32, u32), String> {
    let img = image::load_from_memory(include_bytes!("../assets/icon.png"))
        .map_err(|e| format!("아이콘 디코딩 실패: {}", e))?
        .to_rgba8();
    let (w, h) = (img.width(), img.height());
    Ok((img.into_raw(), w, h))
}

fn build_tray() -> Result<TrayMenu, String> {
    let (rgba, w, h) = load_app_icon()?;
    let icon = tray_icon::Icon::from_rgba(rgba, w, h).map_err(|e| e.to_string())?;

    let menu = Menu::new();
    let show_item = MenuItem::new("화면 모니터링 표시", true, None);
    let start_item = MenuItem::new("▶ 모니터링 시작", true, None);
    let stop_item = MenuItem::new("⏹ 모니터링 정지", true, None);
    let quit_item = MenuItem::new("종료", true, None);
    menu.append_items(&[&show_item, &start_item, &stop_item, &quit_item])
        .map_err(|e| e.to_string())?;

    let tray = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip("화면 모니터링")
        .with_icon(icon)
        .build()
        .map_err(|e| e.to_string())?;

    Ok(TrayMenu {
        _tray: tray,
        show_item,
        start_item,
        stop_item,
        quit_item,
    })
}

struct App {
    shared: Arc<Shared>,
    rolling_cleanup: Arc<RollingCleanup>,
    tray: Option<TrayMenu>,

    // UI 입력 상태
    interval_text: String,
    interval_info: String,
    interval_info_error: bool,
    cleanup_enabled: bool,
    cleanup_age_text: String,
    cleanup_unit_hours: bool, // true=시간, false=분
    cleanup_warning: String,
    cleanup_timer_start: Option<Instant>,
    quality: u8,
    started_at: Instant,
    hidden_after_start: bool,
    window_visible: bool,
}

impl App {
    fn new(cc: &eframe::CreationContext) -> Self {
        setup_korean_fonts(&cc.egui_ctx);

        let save_folder = PathBuf::from("screenshots");
        let _ = std::fs::create_dir_all(&save_folder);

        let shared = Arc::new(Shared {
            capturing: AtomicBool::new(false),
            capture_count: AtomicU64::new(0),
            status: Mutex::new("대기 중...".into()),
            settings: Mutex::new(CaptureSettings {
                interval_secs: 2.0,
                save_folder: save_folder.clone(),
                webp: false,
                quality: 15,
                resolution: "원본".into(),
                grayscale: false,
            }),
            cleanup_enabled: AtomicBool::new(true),
            quit: AtomicBool::new(false),
        });
        logger::info("프로그램 시작됨");
        logger::info(&format!("프로그램 PID: {}", std::process::id()));

        let rolling_cleanup = RollingCleanup::new(save_folder, 24 * 3600);

        // 트레이 아이콘 설정 및 메뉴 이벤트 전달 채널
        let tray = match build_tray() {
            Ok(t) => Some(t),
            Err(e) => {
                logger::error(&format!("시스템 트레이 설정 실패: {}", e));
                None
            }
        };
        // 창 HWND 확보 (숨김 상태에서 트레이 메뉴로 창을 다시 띄우기 위해 필요)
        let hwnd: isize = {
            use raw_window_handle::{HasWindowHandle, RawWindowHandle};
            match cc.window_handle().map(|h| h.as_raw()) {
                Ok(RawWindowHandle::Win32(w)) => w.hwnd.get(),
                _ => 0,
            }
        };
        logger::info(&format!("메인 창 HWND: {}", hwnd));

        // 창이 숨겨진 동안에는 egui update()가 실행되지 않으므로,
        // 트레이 메뉴 이벤트는 이 핸들러(메인 스레드 메시지 루프)에서 직접 처리한다.
        if let Some(t) = &tray {
            TRAY_ITEMS.with(|cell| {
                *cell.borrow_mut() = Some((t.start_item.clone(), t.stop_item.clone()));
            });
            let ids = (
                t.show_item.id().clone(),
                t.start_item.id().clone(),
                t.stop_item.id().clone(),
                t.quit_item.id().clone(),
            );
            let shared2 = Arc::clone(&shared);
            let cleanup2 = Arc::clone(&rolling_cleanup);
            let ctx = cc.egui_ctx.clone();
            MenuEvent::set_event_handler(Some(move |event: MenuEvent| {
                if event.id == ids.0 {
                    // 화면 모니터링 표시
                    show_window_raw(hwnd);
                    ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
                    ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
                } else if event.id == ids.1 {
                    start_capture_shared(&shared2, &cleanup2, &ctx);
                } else if event.id == ids.2 {
                    stop_capture_shared(&shared2, &cleanup2);
                } else if event.id == ids.3 {
                    // 종료: 창을 깨운 뒤 종료 플래그와 함께 Close 처리
                    logger::info("프로그램 종료 시작");
                    stop_capture_shared(&shared2, &cleanup2);
                    shared2.quit.store(true, Ordering::SeqCst);
                    show_window_raw(hwnd);
                    ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
                    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                }
                ctx.request_repaint();
            }));
        }

        let mut app = Self {
            shared,
            rolling_cleanup,
            tray,
            interval_text: "2.0".into(),
            interval_info: "범위: 0.1 ~ 3600초 (1시간)".into(),
            interval_info_error: false,
            cleanup_enabled: true,
            cleanup_age_text: "24".into(),
            cleanup_unit_hours: true,
            cleanup_warning: String::new(),
            cleanup_timer_start: None,
            quality: 15,
            started_at: Instant::now(),
            hidden_after_start: false,
            window_visible: true,
        };

        // GUI 로드 후 자동으로 캡처 시작
        app.start_capture(&cc.egui_ctx);
        app
    }

    fn cleanup_age_secs(&self) -> Option<u64> {
        let value: f64 = self.cleanup_age_text.trim().parse().ok()?;
        if value <= 0.0 {
            return None;
        }
        if self.cleanup_unit_hours {
            if !(1.0..=525600.0).contains(&value) {
                return None;
            }
            Some((value * 3600.0) as u64)
        } else {
            if !(1.0..=60.0).contains(&value) {
                return None;
            }
            Some((value * 60.0) as u64)
        }
    }

    fn validate_cleanup(&mut self) -> bool {
        if self.cleanup_age_text.trim().is_empty() {
            self.cleanup_warning = "삭제 주기를 입력해주세요. 숫자만 입력 가능합니다.".into();
            return false;
        }
        if self.cleanup_age_text.trim().parse::<f64>().is_err() {
            self.cleanup_warning = "유효한 숫자를 입력해주세요. 예: 30, 2.5".into();
            return false;
        }
        match self.cleanup_age_secs() {
            Some(_) => {
                self.cleanup_warning.clear();
                true
            }
            None => {
                self.cleanup_warning = if self.cleanup_unit_hours {
                    "삭제 주기는 1시간에서 525600시간(365일) 사이로 설정해야 합니다.".into()
                } else {
                    "삭제 주기는 1분에서 60분(1시간) 사이로 설정해야 합니다.".into()
                };
                false
            }
        }
    }

    fn start_capture(&mut self, ctx: &egui::Context) {
        if self.shared.capturing.load(Ordering::Relaxed) {
            return;
        }
        // 간격 유효성 검사
        let interval: f64 = match self.interval_text.trim().parse() {
            Ok(v) if (0.1..=3600.0).contains(&v) => v,
            _ => {
                self.interval_info = "❌ 올바른 간격을 입력하세요 (0.1-3600초)".into();
                self.interval_info_error = true;
                return;
            }
        };
        if self.cleanup_enabled && !self.validate_cleanup() {
            return;
        }

        {
            let mut s = self.shared.settings.lock().unwrap();
            s.interval_secs = interval;
            s.quality = self.quality;
        }
        self.shared
            .cleanup_enabled
            .store(self.cleanup_enabled, Ordering::Relaxed);
        if self.cleanup_enabled {
            if let Some(secs) = self.cleanup_age_secs() {
                self.rolling_cleanup.update_cleanup_age(secs);
            }
        }
        start_capture_shared(&self.shared, &self.rolling_cleanup, ctx);
    }

    fn stop_capture(&mut self) {
        stop_capture_shared(&self.shared, &self.rolling_cleanup);
    }

    fn quit(&mut self, ctx: &egui::Context) {
        logger::info("프로그램 종료 시작");
        self.stop_capture();
        self.shared.quit.store(true, Ordering::SeqCst);
        TRAY_ITEMS.with(|cell| *cell.borrow_mut() = None);
        self.tray = None;
        logger::info("프로그램 종료 확인됨");
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // 시작 0.5초 후 트레이로 숨김 (watch.py의 run()과 동일)
        if !self.hidden_after_start && self.started_at.elapsed() > Duration::from_millis(500) {
            self.hidden_after_start = true;
            self.window_visible = false;
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }

        // 트레이 핸들러가 캡처/삭제 상태를 바꿨을 수 있으므로 UI 타이머 상태를 동기화
        let cleanup_running = self.rolling_cleanup.is_running();
        if cleanup_running && self.cleanup_timer_start.is_none() {
            self.cleanup_timer_start = Some(Instant::now());
        } else if !cleanup_running {
            self.cleanup_timer_start = None;
        }

        // 트레이 종료 메뉴가 종료 플래그를 세웠으면 즉시 종료 처리
        if self.shared.quit.load(Ordering::Relaxed) && self.tray.is_some() {
            self.tray = None;
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }

        // 창 닫기 -> 숨김 처리 (종료 플래그가 없을 때)
        if ctx.input(|i| i.viewport().close_requested())
            && !self.shared.quit.load(Ordering::Relaxed)
        {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.window_visible = false;
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }

        // 트레이 이벤트를 계속 받기 위해 주기적으로 깨어남
        ctx.request_repaint_after(Duration::from_millis(500));

        let capturing = self.shared.capturing.load(Ordering::Relaxed);

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.vertical_centered(|ui| {
                    ui.heading("화면 모니터링");
                });
                ui.add_space(8.0);

                // 캡처 간격 설정
                ui.group(|ui| {
                    ui.label(egui::RichText::new("캡처 간격 설정").strong());
                    ui.horizontal(|ui| {
                        ui.label("간격:");
                        ui.add_enabled(
                            !capturing,
                            egui::TextEdit::singleline(&mut self.interval_text)
                                .desired_width(80.0),
                        );
                        ui.label("초");
                        if ui
                            .add_enabled(!capturing, egui::Button::new("적용"))
                            .clicked()
                        {
                            match self.interval_text.trim().parse::<f64>() {
                                Ok(v) if (0.1..=3600.0).contains(&v) => {
                                    self.shared.settings.lock().unwrap().interval_secs = v;
                                    self.interval_info = format!("✅ {}초로 설정됨", v);
                                    self.interval_info_error = false;
                                }
                                Ok(_) => {
                                    self.interval_info =
                                        "❌ 범위를 벗어남: 0.1 ~ 3600초".into();
                                    self.interval_info_error = true;
                                }
                                Err(_) => {
                                    self.interval_info = "❌ 올바른 숫자를 입력하세요".into();
                                    self.interval_info_error = true;
                                }
                            }
                        }
                    });
                    let color = if self.interval_info_error {
                        egui::Color32::RED
                    } else {
                        egui::Color32::GRAY
                    };
                    ui.label(egui::RichText::new(&self.interval_info).color(color).small());
                });

                // 저장 경로 설정
                ui.group(|ui| {
                    ui.label(egui::RichText::new("저장 경로 설정").strong());
                    let folder = self.shared.settings.lock().unwrap().save_folder.clone();
                    let abs = std::fs::canonicalize(&folder).unwrap_or(folder.clone());
                    let mut display = abs.display().to_string();
                    if let Some(stripped) = display.strip_prefix("\\\\?\\") {
                        display = stripped.to_string();
                    }
                    if display.chars().count() > 50 {
                        let tail: String = display
                            .chars()
                            .rev()
                            .take(47)
                            .collect::<Vec<_>>()
                            .into_iter()
                            .rev()
                            .collect();
                        display = format!("...{}", tail);
                    }
                    ui.label(
                        egui::RichText::new(format!("현재 경로: {}", display))
                            .color(egui::Color32::from_rgb(0, 100, 220)),
                    );
                    if ui
                        .add_enabled(!capturing, egui::Button::new("경로 변경"))
                        .clicked()
                    {
                        if let Some(new_path) = rfd::FileDialog::new()
                            .set_title("스크린샷 저장 경로 선택")
                            .set_directory(&folder)
                            .pick_folder()
                        {
                            let _ = std::fs::create_dir_all(&new_path);
                            self.shared.settings.lock().unwrap().save_folder =
                                new_path.clone();
                            self.rolling_cleanup.set_save_folder(new_path.clone());
                            logger::info(&format!("저장 경로 변경됨: {:?}", new_path));
                        }
                    }
                });

                // 자동 삭제 설정
                ui.group(|ui| {
                    ui.label(egui::RichText::new("자동 삭제 설정").strong());
                    let checkbox = ui.add_enabled(
                        !capturing,
                        egui::Checkbox::new(
                            &mut self.cleanup_enabled,
                            "삭제 활성화 (지정된 시간보다 오래된 파일 자동 삭제)",
                        ),
                    );
                    if checkbox.changed() {
                        if self.cleanup_enabled {
                            if !self.validate_cleanup() {
                                self.cleanup_enabled = false;
                            }
                        } else if self.rolling_cleanup.is_running() {
                            self.rolling_cleanup.stop();
                            self.cleanup_timer_start = None;
                        }
                        self.shared
                            .cleanup_enabled
                            .store(self.cleanup_enabled, Ordering::Relaxed);
                    }
                    ui.horizontal(|ui| {
                        ui.label("지정 시간:");
                        let edit = ui.add_enabled(
                            !capturing,
                            egui::TextEdit::singleline(&mut self.cleanup_age_text)
                                .desired_width(60.0),
                        );
                        let mut unit_changed = false;
                        egui::ComboBox::from_id_source("cleanup_unit")
                            .selected_text(if self.cleanup_unit_hours { "시간" } else { "분" })
                            .width(60.0)
                            .show_ui(ui, |ui| {
                                if !capturing {
                                    unit_changed |= ui
                                        .selectable_value(
                                            &mut self.cleanup_unit_hours,
                                            false,
                                            "분",
                                        )
                                        .changed();
                                    unit_changed |= ui
                                        .selectable_value(
                                            &mut self.cleanup_unit_hours,
                                            true,
                                            "시간",
                                        )
                                        .changed();
                                }
                            });
                        if (edit.changed() || unit_changed) && self.cleanup_enabled {
                            if self.validate_cleanup() {
                                if let Some(secs) = self.cleanup_age_secs() {
                                    if self.rolling_cleanup.is_running() {
                                        self.rolling_cleanup.update_cleanup_age(secs);
                                    }
                                }
                            }
                        }
                    });
                    if !self.cleanup_warning.is_empty() {
                        ui.label(
                            egui::RichText::new(&self.cleanup_warning)
                                .color(egui::Color32::RED),
                        );
                    }
                    let info = if self.cleanup_enabled {
                        format!(
                            "{}{}을 초과한 파일을 10분마다 삭제",
                            self.cleanup_age_text.trim(),
                            if self.cleanup_unit_hours { "시간" } else { "분" }
                        )
                    } else {
                        "자동 삭제가 비활성화되었습니다".into()
                    };
                    ui.label(
                        egui::RichText::new(info).color(egui::Color32::from_rgb(0, 100, 220)),
                    );
                    // 다음 삭제까지 남은 시간
                    let timer_text = match self.cleanup_timer_start {
                        Some(start) if self.cleanup_enabled => {
                            let elapsed = start.elapsed().as_secs() % CLEANUP_TIMER_SECS;
                            let remain = CLEANUP_TIMER_SECS - elapsed;
                            format!("다음 삭제까지: {:02}분 {:02}초", remain / 60, remain % 60)
                        }
                        _ => "다음 삭제까지: --".into(),
                    };
                    ui.label(
                        egui::RichText::new(timer_text)
                            .strong()
                            .color(egui::Color32::from_rgb(0, 0, 139)),
                    );
                });

                // 이미지 설정
                ui.group(|ui| {
                    ui.label(egui::RichText::new("이미지 설정").strong());
                    let mut settings = self.shared.settings.lock().unwrap();
                    ui.horizontal(|ui| {
                        ui.label("포맷:");
                        ui.radio_value(&mut settings.webp, false, "JPEG");
                        ui.radio_value(&mut settings.webp, true, "WebP");
                    });
                    ui.horizontal(|ui| {
                        ui.label("품질:");
                        if ui
                            .add(egui::Slider::new(&mut self.quality, 1..=100))
                            .changed()
                        {
                            settings.quality = self.quality;
                        }
                    });
                    ui.horizontal(|ui| {
                        ui.label("해상도:");
                        egui::ComboBox::from_id_source("resolution")
                            .selected_text(&settings.resolution)
                            .show_ui(ui, |ui| {
                                for r in ["원본", "1920x1080", "1280x720", "1024x768", "800x600"]
                                {
                                    ui.selectable_value(
                                        &mut settings.resolution,
                                        r.to_string(),
                                        r,
                                    );
                                }
                            });
                    });
                    ui.checkbox(&mut settings.grayscale, "흑백 변환");
                });

                ui.add_space(6.0);
                ui.vertical_centered(|ui| {
                    ui.label(self.shared.status.lock().unwrap().clone());
                    ui.label(format!(
                        "캡처된 이미지: {}개",
                        self.shared.capture_count.load(Ordering::Relaxed)
                    ));
                });

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    let btn_size = egui::vec2(160.0, 40.0);
                    if ui
                        .add_sized(
                            btn_size,
                            egui::Button::new(if capturing { "캡처 정지" } else { "캡처 시작" }),
                        )
                        .clicked()
                    {
                        if capturing {
                            self.stop_capture();
                        } else {
                            self.start_capture(ctx);
                        }
                    }
                    if ui
                        .add_sized(btn_size, egui::Button::new("프로그램 종료"))
                        .clicked()
                    {
                        self.quit(ctx);
                    }
                });
            });
        });
    }
}

/// 한글 표시를 위해 맑은 고딕을 egui 폰트에 추가
fn setup_korean_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    for name in ["malgun.ttf", "gulim.ttc"] {
        let path = std::path::Path::new("C:\\Windows\\Fonts").join(name);
        if let Ok(data) = std::fs::read(&path) {
            fonts
                .font_data
                .insert("korean".into(), egui::FontData::from_owned(data));
            fonts
                .families
                .get_mut(&egui::FontFamily::Proportional)
                .unwrap()
                .push("korean".into());
            fonts
                .families
                .get_mut(&egui::FontFamily::Monospace)
                .unwrap()
                .push("korean".into());
            break;
        }
    }
    ctx.set_fonts(fonts);
}

fn main() -> eframe::Result<()> {
    let mut viewport = egui::ViewportBuilder::default()
        .with_inner_size([450.0, 800.0])
        .with_resizable(false)
        .with_title("화면 모니터링");
    if let Ok((rgba, w, h)) = load_app_icon() {
        viewport = viewport.with_icon(egui::IconData {
            rgba,
            width: w,
            height: h,
        });
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };
    eframe::run_native(
        "화면 모니터링",
        options,
        Box::new(|cc| Box::new(App::new(cc))),
    )
}
