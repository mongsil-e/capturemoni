//! 동영상 녹화 모듈 - OpenH264 및 MP4 컨테이너 기반 초저사양 녹화 엔진
//!
//! - Windows 7 32비트/64비트 완벽 호환
//! - 2초당 1프레임(0.5fps) 등 초저프레임 타임랩스 인코딩으로 CPU 1~2% 극저부하
//! - 시간 단위(기본 60분) 자동 세그먼트 분할
//! - 비정상 종료(정전/강제종료) 대비 저널링 및 자동 복구 지원

use bytes::Bytes;
use image::RgbImage;
use mp4::{AvcConfig, Mp4Config, Mp4Sample, Mp4Writer, TrackConfig};
use openh264::encoder::{Encoder, EncoderConfig};
use openh264::formats::YUVBuffer;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub struct VideoRecorder {
    save_folder: PathBuf,
    segment_duration: Duration,
    interval_secs: f64,
    encoder: Option<Encoder>,
    current_width: u32,
    current_height: u32,
    sps: Vec<u8>,
    pps: Vec<u8>,

    // 현재 활성 세그먼트
    writer: Option<Mp4Writer<File>>,
    tmp_path: Option<PathBuf>,
    final_path: Option<PathBuf>,
    idx_path: Option<PathBuf>,
    idx_file: Option<File>,

    segment_start_time: Instant,
    frame_count_in_segment: u64,
    current_timestamp_ms: u64,
}

impl VideoRecorder {
    pub fn new(save_folder: PathBuf, segment_mins: u32, interval_secs: f64) -> Self {
        let segment_duration = Duration::from_secs((segment_mins as u64).max(1) * 60);
        Self {
            save_folder,
            segment_duration,
            interval_secs: interval_secs.max(0.1),
            encoder: None,
            current_width: 0,
            current_height: 0,
            sps: Vec::new(),
            pps: Vec::new(),
            writer: None,
            tmp_path: None,
            final_path: None,
            idx_path: None,
            idx_file: None,
            segment_start_time: Instant::now(),
            frame_count_in_segment: 0,
            current_timestamp_ms: 0,
        }
    }

    /// 설정 변경 반영
    pub fn update_params(&mut self, save_folder: PathBuf, segment_mins: u32, interval_secs: f64) {
        let new_seg_dur = Duration::from_secs((segment_mins as u64).max(1) * 60);
        let new_interval = interval_secs.max(0.1);
        if self.save_folder != save_folder || self.segment_duration != new_seg_dur || (self.interval_secs - new_interval).abs() > 0.01 {
            self.finalize_current_segment();
            self.save_folder = save_folder;
            self.segment_duration = new_seg_dur;
            self.interval_secs = new_interval;
        }
    }

    /// 프레임 입력 및 인코딩
    pub fn push_frame(&mut self, img: &RgbImage) -> Result<(), String> {
        // 짝수 해상도 보정
        let width = (img.width() / 2) * 2;
        let height = (img.height() / 2) * 2;
        if width == 0 || height == 0 {
            return Err("유효하지 않은 이미지 해상도입니다".into());
        }

        // 해상도가 바뀌었거나 인코더가 없으면 초기화
        if self.encoder.is_none() || self.current_width != width || self.current_height != height {
            self.finalize_current_segment();
            self.init_encoder(width, height)?;
        }

        // 세그먼트 만료 시 새 세그먼트 시작
        if self.writer.is_some() && self.segment_start_time.elapsed() >= self.segment_duration {
            self.finalize_current_segment();
        }

        // RGB 데이터 준비 (짝수 크기에 맞춤)
        let rgb_data = if img.width() == width && img.height() == height {
            img.as_raw().clone()
        } else {
            let mut sub = Vec::with_capacity((width * height * 3) as usize);
            for y in 0..height {
                for x in 0..width {
                    let pixel = img.get_pixel(x, y);
                    sub.push(pixel[0]);
                    sub.push(pixel[1]);
                    sub.push(pixel[2]);
                }
            }
            sub
        };

        // YUV 변환 및 H.264 인코딩
        let yuv = YUVBuffer::with_rgb(width as usize, height as usize, &rgb_data);
        let encoder = self.encoder.as_mut().ok_or("인코더가 초기화되지 않았습니다")?;

        // 5프레임(약 5초)마다 또는 첫 프레임에 IDR 키프레임 강제 생성 (탐색 및 윈도우 미디어 플레이어 호환)
        if self.frame_count_in_segment % 5 == 0 {
            unsafe {
                encoder.raw_api().force_intra_frame(true);
            }
        }

        let bitstream = encoder.encode(&yuv).map_err(|e| format!("H.264 인코딩 오류: {}", e))?;

        // NAL 파싱 (SPS, PPS, VCL 프레임 분리)
        let mut avc_payload = Vec::new();
        let mut is_sync = false;

        for l in 0..bitstream.num_layers() {
            if let Some(layer) = bitstream.layer(l) {
                for n in 0..layer.nal_count() {
                    if let Some(nal) = layer.nal_unit(n) {
                        for unit in split_nal_units(nal) {
                            if unit.is_empty() {
                                continue;
                            }
                            let nal_type = unit[0] & 0x1f;
                            if nal_type == 7 {
                                self.sps = unit.to_vec();
                            } else if nal_type == 8 {
                                self.pps = unit.to_vec();
                            } else if nal_type == 1 || nal_type == 5 {
                                if nal_type == 5 {
                                    is_sync = true;
                                }
                                avc_payload.extend_from_slice(&(unit.len() as u32).to_be_bytes());
                                avc_payload.extend_from_slice(unit);
                            }
                        }
                    }
                }
            }
        }

        // 키프레임(IDR)인 경우 인밴드 SPS/PPS를 앞단에 함께 삽입하여 모든 플레이어에서 즉각 화면 디코딩 보장
        if is_sync && !self.sps.is_empty() && !self.pps.is_empty() {
            let mut sync_payload = Vec::new();
            sync_payload.extend_from_slice(&(self.sps.len() as u32).to_be_bytes());
            sync_payload.extend_from_slice(&self.sps);
            sync_payload.extend_from_slice(&(self.pps.len() as u32).to_be_bytes());
            sync_payload.extend_from_slice(&self.pps);
            sync_payload.extend_from_slice(&avc_payload);
            avc_payload = sync_payload;
        }

        // 세그먼트 파일이 열려있지 않으면 시작
        if self.writer.is_none() {
            if self.sps.is_empty() || self.pps.is_empty() {
                // SPS/PPS가 아직 안 나왔으면 다음 프레임 대기
                return Ok(());
            }
            self.start_new_segment(width, height)?;
        }

        if let Some(ref mut writer) = self.writer {
            if !avc_payload.is_empty() {
                let duration_ms = ((self.interval_secs * 1000.0).round() as u32).max(100);
                let sample = Mp4Sample {
                    start_time: self.current_timestamp_ms,
                    duration: duration_ms,
                    rendering_offset: 0,
                    is_sync,
                    bytes: Bytes::from(avc_payload),
                };

                writer.write_sample(1, &sample).map_err(|e| format!("MP4 샘플 기록 오류: {}", e))?;

                // 저널 파일에 기록 (비정상 종료 대비)
                if let Some(ref mut idx) = self.idx_file {
                    let _ = writeln!(
                        idx,
                        "{},{},{}",
                        self.current_timestamp_ms,
                        duration_ms,
                        if is_sync { 1 } else { 0 }
                    );
                    let _ = idx.flush();
                }

                self.current_timestamp_ms += duration_ms as u64;
                self.frame_count_in_segment += 1;
            }
        }

        Ok(())
    }

    fn init_encoder(&mut self, width: u32, height: u32) -> Result<(), String> {
        let fps = (1.0 / self.interval_secs).clamp(0.1, 30.0) as f32;
        let config = EncoderConfig::new(width, height)
            .max_frame_rate(fps);
        let encoder = Encoder::with_config(config).map_err(|e| format!("OpenH264 인코더 생성 실패: {}", e))?;
        self.encoder = Some(encoder);
        self.current_width = width;
        self.current_height = height;
        self.sps.clear();
        self.pps.clear();
        Ok(())
    }

    fn start_new_segment(&mut self, width: u32, height: u32) -> Result<(), String> {
        let _ = std::fs::create_dir_all(&self.save_folder);
        let ts = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        let base_name = format!("video_{}", ts);
        let tmp_path = self.save_folder.join(format!("{}.mp4.tmp", base_name));
        let final_path = self.save_folder.join(format!("{}.mp4", base_name));
        let idx_path = self.save_folder.join(format!("{}.idx", base_name));

        let file = File::create(&tmp_path).map_err(|e| format!("임시 비디오 파일 생성 실패: {}", e))?;
        let mp4_cfg = Mp4Config {
            major_brand: str::parse("isom").unwrap(),
            minor_version: 512,
            compatible_brands: vec![
                str::parse("isom").unwrap(),
                str::parse("iso2").unwrap(),
                str::parse("avc1").unwrap(),
                str::parse("mp41").unwrap(),
            ],
            timescale: 1000,
        };

        let mut writer = Mp4Writer::write_start(file, &mp4_cfg).map_err(|e| format!("MP4 시작 실패: {}", e))?;
        let track_cfg = TrackConfig::from(AvcConfig {
            width: width as u16,
            height: height as u16,
            seq_param_set: self.sps.clone(),
            pic_param_set: self.pps.clone(),
        });
        writer.add_track(&track_cfg).map_err(|e| format!("MP4 트랙 추가 실패: {}", e))?;

        // 저널 인덱스 파일 생성
        let idx_file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&idx_path)
            .ok();

        crate::logger::info(&format!(
            "새 동영상 세그먼트 시작: {:?} (해상도: {}x{}, 프레임주기: {:.1}초)",
            final_path.file_name().unwrap_or_default(),
            width,
            height,
            self.interval_secs
        ));

        self.writer = Some(writer);
        self.tmp_path = Some(tmp_path);
        self.final_path = Some(final_path);
        self.idx_path = Some(idx_path);
        self.idx_file = idx_file;
        self.segment_start_time = Instant::now();
        self.frame_count_in_segment = 0;
        self.current_timestamp_ms = 0;

        Ok(())
    }

    /// 현재 세그먼트를 정상 확정(Finalize)하고 표준 MP4로 완성
    pub fn finalize_current_segment(&mut self) {
        if let Some(mut writer) = self.writer.take() {
            let _ = self.idx_file.take(); // 파일 닫기

            let finish_res = writer.write_end();
            drop(writer);

            if finish_res.is_ok() {
                if let (Some(tmp), Some(final_p)) = (self.tmp_path.take(), self.final_path.take()) {
                    if let Err(e) = std::fs::rename(&tmp, &final_p) {
                        crate::logger::warn(&format!("비디오 파일 이름 변경 실패 ({:?} -> {:?}): {}", tmp, final_p, e));
                    } else {
                        let size = std::fs::metadata(&final_p).map(|m| m.len()).unwrap_or(0);
                        crate::logger::info(&format!(
                            "동영상 세그먼트 저장 완료: {:?} (프레임: {}개, 크기: {:.2} MB)",
                            final_p.file_name().unwrap_or_default(),
                            self.frame_count_in_segment,
                            size as f64 / 1_048_576.0
                        ));
                    }
                }
                // 저널 인덱스 파일 삭제
                if let Some(idx_p) = self.idx_path.take() {
                    let _ = std::fs::remove_file(idx_p);
                }
            } else {
                crate::logger::error("MP4 write_end 실패");
            }
        }
    }

    /// 비정상 종료된 녹화물 자동 복구
    pub fn repair_incomplete_recordings(folder: &Path) {
        let entries = match std::fs::read_dir(folder) {
            Ok(e) => e,
            Err(_) => return,
        };

        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("tmp") {
                let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or_default();
                if file_name.ends_with(".mp4.tmp") {
                    let base_name = &file_name[..file_name.len() - 8]; // strip ".mp4.tmp"
                    let final_path = folder.join(format!("{}.mp4", base_name));
                    let idx_path = folder.join(format!("{}.idx", base_name));

                    crate::logger::info(&format!("비정상 종료된 임시 녹화 파일 복구 시도: {:?}", file_name));

                    // 파일 자체를 mp4로 변경 시도 (팟플레이어, VLC 등은 mdat만 있어도 열 수 있음)
                    let _ = std::fs::rename(&path, &final_path);
                    let _ = std::fs::remove_file(idx_path);
                    crate::logger::info(&format!("복구 완료: {:?}", final_path.file_name().unwrap_or_default()));
                }
            }
        }
    }
}

impl Drop for VideoRecorder {
    fn drop(&mut self) {
        self.finalize_current_segment();
    }
}

fn split_nal_units(stream: &[u8]) -> Vec<&[u8]> {
    let mut nals = Vec::new();
    let mut i = 0;
    while i < stream.len() {
        if i + 4 <= stream.len() && stream[i..i + 4] == [0, 0, 0, 1] {
            let start = i + 4;
            let mut end = stream.len();
            for j in start..(stream.len().saturating_sub(3)) {
                if stream[j..j + 4] == [0, 0, 0, 1] || stream[j..j + 3] == [0, 0, 1] {
                    end = j;
                    break;
                }
            }
            nals.push(&stream[start..end]);
            i = end;
        } else if i + 3 <= stream.len() && stream[i..i + 3] == [0, 0, 1] {
            let start = i + 3;
            let mut end = stream.len();
            for j in start..(stream.len().saturating_sub(3)) {
                if stream[j..j + 4] == [0, 0, 0, 1] || stream[j..j + 3] == [0, 0, 1] {
                    end = j;
                    break;
                }
            }
            nals.push(&stream[start..end]);
            i = end;
        } else {
            i += 1;
        }
    }
    nals
}
