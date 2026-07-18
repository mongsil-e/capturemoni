//! GDI 기반 전체 화면 캡처, 타임스탬프 오버레이, 이미지 저장

use image::{DynamicImage, RgbImage};
use rusttype::{point, Font, Scale};
use std::fs;
use std::path::Path;

use windows_sys::Win32::Foundation::{BOOL, LPARAM, RECT};
use windows_sys::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleDC, CreateDCW, CreateDIBSection, DeleteDC, DeleteObject,
    EnumDisplayMonitors, GetDIBits, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    CAPTUREBLT, DIB_RGB_COLORS, HDC, HMONITOR, SRCCOPY,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

/// EnumDisplayMonitors 콜백 - 모니터 영역을 수집한다
unsafe extern "system" fn monitor_enum_proc(
    _monitor: HMONITOR,
    _dc: HDC,
    rect: *mut RECT,
    lparam: LPARAM,
) -> BOOL {
    let rects = &mut *(lparam as *mut Vec<RECT>);
    rects.push(*rect);
    1
}

/// 연결된 모든 모니터의 영역 목록
pub fn monitor_rects() -> Vec<RECT> {
    let mut rects: Vec<RECT> = Vec::new();
    unsafe {
        EnumDisplayMonitors(
            0,
            std::ptr::null(),
            Some(monitor_enum_proc),
            &mut rects as *mut Vec<RECT> as LPARAM,
        );
    }
    rects
}

/// 전체 가상 화면을 RGB 이미지로 캡처한다.
pub fn grab_screen() -> Result<RgbImage, String> {
    unsafe {
        let x = GetSystemMetrics(SM_XVIRTUALSCREEN);
        let y = GetSystemMetrics(SM_YVIRTUALSCREEN);
        let w = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        let h = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        if w <= 0 || h <= 0 {
            return Err("화면 크기를 가져올 수 없습니다".into());
        }

        let display: Vec<u16> = "DISPLAY\0".encode_utf16().collect();
        let screen_dc = CreateDCW(display.as_ptr(), std::ptr::null(), std::ptr::null(), std::ptr::null());
        if screen_dc == 0 {
            return Err("화면 DC 생성 실패".into());
        }
        let mem_dc = CreateCompatibleDC(screen_dc);
        if mem_dc == 0 {
            DeleteDC(screen_dc);
            return Err("메모리 DC 생성 실패".into());
        }

        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = w;
        bmi.bmiHeader.biHeight = -h; // top-down
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        let mut bits: *mut core::ffi::c_void = std::ptr::null_mut();
        let bitmap = CreateDIBSection(mem_dc, &bmi, DIB_RGB_COLORS, &mut bits, 0, 0);
        if bitmap == 0 {
            DeleteDC(mem_dc);
            DeleteDC(screen_dc);
            return Err("DIB 섹션 생성 실패".into());
        }

        let old = SelectObject(mem_dc, bitmap);
        // 모니터별로 각각 캡처해 가상 화면 좌표 기준으로 합성한다 (멀티 모니터 전체 캡처).
        let monitors = monitor_rects();
        let ok = if monitors.is_empty() {
            // 모니터 열거 실패 시 가상 화면 전체를 한 번에 캡처
            BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, SRCCOPY | CAPTUREBLT)
        } else {
            let mut all_ok = 1;
            for rc in &monitors {
                let mw = rc.right - rc.left;
                let mh = rc.bottom - rc.top;
                if BitBlt(
                    mem_dc,
                    rc.left - x,
                    rc.top - y,
                    mw,
                    mh,
                    screen_dc,
                    rc.left,
                    rc.top,
                    SRCCOPY | CAPTUREBLT,
                ) == 0
                {
                    all_ok = 0;
                }
            }
            all_ok
        };
        SelectObject(mem_dc, old);

        let result = if ok == 0 {
            Err("BitBlt 실패".into())
        } else {
            let mut buf = vec![0u8; (w as usize) * (h as usize) * 4];
            let got = GetDIBits(
                mem_dc,
                bitmap,
                0,
                h as u32,
                buf.as_mut_ptr() as *mut _,
                &mut bmi,
                DIB_RGB_COLORS,
            );
            if got == 0 {
                Err("GetDIBits 실패".into())
            } else {
                // BGRA -> RGB
                let mut rgb = Vec::with_capacity((w as usize) * (h as usize) * 3);
                for px in buf.chunks_exact(4) {
                    rgb.push(px[2]);
                    rgb.push(px[1]);
                    rgb.push(px[0]);
                }
                RgbImage::from_raw(w as u32, h as u32, rgb)
                    .ok_or_else(|| "이미지 버퍼 생성 실패".to_string())
            }
        };

        DeleteObject(bitmap);
        DeleteDC(mem_dc);
        DeleteDC(screen_dc);
        result
    }
}

/// 시스템 폰트를 로드한다 (malgun.ttf 우선, arial.ttf 대체).
pub fn load_font() -> Option<Font<'static>> {
    for name in ["malgun.ttf", "arial.ttf"] {
        let path = Path::new("C:\\Windows\\Fonts").join(name);
        if let Ok(data) = fs::read(&path) {
            if let Some(font) = Font::try_from_vec(data) {
                return Some(font);
            }
        }
    }
    None
}

/// 이미지 좌상단에 현재 시간 텍스트를 반투명 검은 배경과 함께 그린다.
pub fn add_timestamp_overlay(img: &mut RgbImage, font: Option<&Font>) {
    let text = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let font = match font {
        Some(f) => f,
        None => return,
    };
    let scale = Scale::uniform(24.0);
    let v_metrics = font.v_metrics(scale);
    let glyphs: Vec<_> = font
        .layout(&text, scale, point(0.0, v_metrics.ascent))
        .collect();
    let text_width = glyphs
        .last()
        .map(|g| g.position().x + g.unpositioned().h_metrics().advance_width)
        .unwrap_or(0.0)
        .ceil() as u32;
    let text_height = (v_metrics.ascent - v_metrics.descent).ceil() as u32;

    let padding = 10u32;
    let (x1, y1) = (10u32, 10u32);
    let x2 = x1 + text_width + padding * 2;
    let y2 = y1 + text_height + padding * 2;

    // 반투명(약 70%) 검은 배경
    for py in y1..y2.min(img.height()) {
        for px in x1..x2.min(img.width()) {
            let p = img.get_pixel_mut(px, py);
            p.0 = [
                (p.0[0] as u32 * 75 / 255) as u8,
                (p.0[1] as u32 * 75 / 255) as u8,
                (p.0[2] as u32 * 75 / 255) as u8,
            ];
        }
    }

    // 흰색 텍스트
    let (tx, ty) = (x1 + padding, y1 + padding);
    for glyph in &glyphs {
        if let Some(bb) = glyph.pixel_bounding_box() {
            glyph.draw(|gx, gy, v| {
                let px = tx as i32 + bb.min.x + gx as i32;
                let py = ty as i32 + bb.min.y + gy as i32;
                if px >= 0 && py >= 0 && (px as u32) < img.width() && (py as u32) < img.height() {
                    let p = img.get_pixel_mut(px as u32, py as u32);
                    let a = (v * 255.0) as u32;
                    for c in 0..3 {
                        p.0[c] = ((p.0[c] as u32 * (255 - a) + 255 * a) / 255) as u8;
                    }
                }
            });
        }
    }
}

pub struct SaveOptions {
    pub webp: bool,
    pub quality: u8,          // 1-100
    pub resolution: Option<(u32, u32)>, // None = 원본
    pub grayscale: bool,
}

/// 옵션을 적용해 이미지를 파일로 저장한다.
pub fn save_image(img: RgbImage, path: &Path, opts: &SaveOptions) -> Result<(), String> {
    let mut dynimg = DynamicImage::ImageRgb8(img);

    if let Some((w, h)) = opts.resolution {
        if dynimg.width() > w || dynimg.height() > h {
            dynimg = dynimg.resize(w, h, image::imageops::FilterType::Lanczos3);
        }
    }
    if opts.grayscale {
        dynimg = DynamicImage::ImageLuma8(dynimg.to_luma8());
    }

    if opts.webp {
        let rgb = dynimg.to_rgb8();
        let encoder = webp::Encoder::from_rgb(rgb.as_raw(), rgb.width(), rgb.height());
        let data = encoder.encode(opts.quality as f32);
        fs::write(path, &*data).map_err(|e| format!("WebP 저장 실패: {}", e))
    } else {
        let file = fs::File::create(path).map_err(|e| format!("파일 생성 실패: {}", e))?;
        let mut writer = std::io::BufWriter::new(file);
        let mut enc =
            image::codecs::jpeg::JpegEncoder::new_with_quality(&mut writer, opts.quality);
        enc.encode_image(&dynimg).map_err(|e| format!("JPEG 저장 실패: {}", e))
    }
}
