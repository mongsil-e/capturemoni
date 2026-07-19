//! settings.json 저장/복원

use serde::{Deserialize, Serialize};

pub const SETTINGS_FILE: &str = "settings.json";

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
#[serde(default)]
pub struct AppSettings {
    pub capture_interval_secs: f64,
    pub save_folder: String,
    pub image_format: String, // "JPEG" | "WEBP"
    pub image_quality: u8,
    pub image_resolution: String,
    pub image_grayscale: bool,
    pub cleanup_enabled: bool,
    pub cleanup_age_value: f64,
    pub cleanup_age_unit: String, // "분" | "시간"
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            capture_interval_secs: 2.0,
            save_folder: "screenshots".into(),
            image_format: "JPEG".into(),
            image_quality: 15,
            image_resolution: "원본".into(),
            image_grayscale: false,
            cleanup_enabled: true,
            cleanup_age_value: 24.0,
            cleanup_age_unit: "시간".into(),
        }
    }
}

impl AppSettings {
    /// 범위를 벗어난 값을 기본 범위로 보정
    pub fn sanitized(mut self) -> Self {
        if !(0.1..=3600.0).contains(&self.capture_interval_secs) {
            self.capture_interval_secs = 2.0;
        }
        self.image_quality = self.image_quality.clamp(1, 100);
        if self.image_format != "WEBP" {
            self.image_format = "JPEG".into();
        }
        const RESOLUTIONS: [&str; 5] = ["원본", "1920x1080", "1280x720", "1024x768", "800x600"];
        if !RESOLUTIONS.contains(&self.image_resolution.as_str()) {
            self.image_resolution = "원본".into();
        }
        if self.cleanup_age_unit != "분" {
            self.cleanup_age_unit = "시간".into();
        }
        let valid_age = if self.cleanup_age_unit == "분" {
            (1.0..=60.0).contains(&self.cleanup_age_value)
        } else {
            (1.0..=525600.0).contains(&self.cleanup_age_value)
        };
        if !valid_age {
            self.cleanup_age_value = 24.0;
            self.cleanup_age_unit = "시간".into();
        }
        if self.save_folder.trim().is_empty() {
            self.save_folder = "screenshots".into();
        }
        self
    }

    pub fn cleanup_age_secs(&self) -> u64 {
        if self.cleanup_age_unit == "분" {
            (self.cleanup_age_value * 60.0) as u64
        } else {
            (self.cleanup_age_value * 3600.0) as u64
        }
    }
}

pub fn load() -> AppSettings {
    match std::fs::read_to_string(SETTINGS_FILE) {
        Ok(text) => match serde_json::from_str::<AppSettings>(&text) {
            Ok(s) => s.sanitized(),
            Err(e) => {
                crate::logger::warn(&format!("settings.json 파싱 실패, 기본값 사용: {}", e));
                AppSettings::default()
            }
        },
        Err(_) => AppSettings::default(),
    }
}

pub fn save(settings: &AppSettings) {
    match serde_json::to_string_pretty(settings) {
        Ok(json) => {
            if let Err(e) = std::fs::write(SETTINGS_FILE, json) {
                crate::logger::warn(&format!("settings.json 저장 실패: {}", e));
            }
        }
        Err(e) => crate::logger::warn(&format!("settings.json 직렬화 실패: {}", e)),
    }
}
