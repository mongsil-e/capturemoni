import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import ImageGrab, ImageDraw, ImageFont, Image
import threading
import time
import os
import psutil
import logging
from datetime import datetime
import pystray
from pystray import MenuItem as item, Menu
import gc


class RollingCleanup:
    """자동 삭제 스레드 클래스 - 10분마다 지정된 시간이 지난 파일 삭제"""

    def __init__(self, save_folder, logger, stop_event, cleanup_age_seconds):
        self.save_folder = save_folder
        self.cleanup_interval_seconds = 600  # 10분 고정 (600초)
        self.cleanup_age_seconds = cleanup_age_seconds
        self.logger = logger
        self.stop_event = stop_event
        self.thread = None
        self.is_running = False
        self.file_lock = threading.Lock()

    def start(self):
        """정리 스레드 시작"""
        if self.is_running:
            return

        self.is_running = True
        # 중지 신호 초기화 (이전 중지 상태 클리어)
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self.thread.start()
        cleanup_age_hours = self.cleanup_age_seconds / 3600
        self.logger.info(f"자동 삭제 스레드 시작됨 (10분마다 {cleanup_age_hours:.1f}시간이 지난 파일 삭제)")

    def stop(self):
        """정리 스레드 중지"""
        self.is_running = False
        # 중지 신호를 먼저 설정하여 스레드가 즉시 반응하도록 함
        self.stop_event.set()

        if self.thread and self.thread.is_alive():
            # 스레드가 종료될 때까지 대기 (개선된 응답성으로 타임아웃 단축)
            self.thread.join(timeout=1.0)  # 2초에서 1초로 단축
            if self.thread.is_alive():
                self.logger.warning("자동 삭제 스레드가 정상적으로 종료되지 않았습니다")
        self.logger.info("자동 삭제 스레드 중지됨")

    def update_cleanup_age(self, new_cleanup_age_seconds):
        """삭제 주기(age)를 동적으로 업데이트"""
        self.cleanup_age_seconds = new_cleanup_age_seconds
        cleanup_age_hours = self.cleanup_age_seconds / 3600
        self.logger.info(f"자동 삭제 주기가 {cleanup_age_hours:.1f}시간으로 업데이트되었습니다.")

    def _cleanup_worker(self):
        """정리 작업자 스레드"""
        while self.is_running and not self.stop_event.is_set():
            try:
                # 설정된 주기만큼 대기 (1초 단위로 나누어 중지 신호 확인 - 응답성 향상)
                remaining_time = self.cleanup_interval_seconds
                check_interval = 1  # 1초마다 중지 신호 확인 (기존 10초에서 개선)

                while remaining_time > 0 and self.is_running and not self.stop_event.is_set():
                    sleep_time = min(check_interval, remaining_time)
                    # time.sleep 대신 stop_event.wait 사용으로 즉시 중단 가능
                    if self.stop_event.wait(timeout=sleep_time):
                        # 중지 신호가 왔으면 즉시 종료
                        break
                    remaining_time -= sleep_time

                if not self.is_running or self.stop_event.is_set():
                    break

                # 지정된 시간이 지난 파일 삭제 실행
                self._perform_rolling_cleanup()

            except Exception as e:
                self.logger.error(f"정리 스레드 오류: {str(e)}")
                # 오류 발생 시 최대 3초 대기 후 재시도 (중지 신호 확인하며)
                for _ in range(3):
                    if not self.is_running or self.stop_event.is_set():
                        return
                    if self.stop_event.wait(timeout=1.0):  # 1초 대기, 중지 신호 확인
                        return

    def _perform_rolling_cleanup(self):
        """지정된 시간이 지난 파일만 삭제"""
        try:
            deleted_count = 0
            failed_count = 0
            current_time = time.time()
            cutoff_time = current_time - self.cleanup_age_seconds # 설정된 시간 사용

            cleanup_age_hours = self.cleanup_age_seconds / 3600
            self.logger.info(f"자동 삭제 시작 - {cleanup_age_hours:.1f}시간이 지난 파일을 스캔하여 삭제합니다...")

            # 파일 시스템 작업 시 락 획득
            with self.file_lock:
                try:
                    # os.scandir()를 사용하여 폴더를 효율적으로 스캔
                    with os.scandir(self.save_folder) as entries:
                        for entry in entries:
                            if not self.is_running or self.stop_event.is_set():
                                break

                            # 이미지 파일만 처리
                            if not entry.name.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.webp')):
                                self.logger.debug(f"건너뜀 (이미지 파일 아님): {entry.name}")
                                continue

                            # 중지 신호 확인 (파일 처리 전에)
                            if not self.is_running or self.stop_event.is_set():
                                break

                            try:
                                # 파일 수정 시간 확인
                                file_mtime = entry.stat().st_mtime

                                # 지정된 시간이 지났는지 확인
                                if file_mtime < cutoff_time:
                                    # 안전한 삭제 시도
                                    if self._safe_delete_file(entry.path):
                                        deleted_count += 1
                                        self.logger.debug(f"삭제됨: {entry.name}")
                                    else:
                                        failed_count += 1
                                        self.logger.warning(f"삭제 실패: {entry.name}")

                                # 중지 신호 확인 (더 빈번한 확인으로 응답성 향상)
                                if not self.is_running or self.stop_event.is_set():
                                    break

                            except (OSError, FileNotFoundError) as e:
                                self.logger.warning(f"파일 처리 중 오류 ({entry.name}): {str(e)}")
                                failed_count += 1
                except OSError as e:
                    self.logger.error(f"폴더 스캔 실패: {str(e)}")
                    return

            # 결과 로깅
            if deleted_count > 0 or failed_count > 0:
                self.logger.info(f"자동 삭제 완료: 삭제 {deleted_count}개, 실패 {failed_count}개")
            else:
                self.logger.debug("자동 삭제 완료: 삭제할 파일 없음")

        except Exception as e:
            self.logger.error(f"자동 삭제 중 오류: {str(e)}")

    def _safe_delete_file(self, file_path, max_retries=3, retry_delay=1):
        """안전한 파일 삭제"""
        for attempt in range(max_retries):
            # 중지 신호 확인
            if not self.is_running or self.stop_event.is_set():
                return False

            try:
                # 파일 삭제 시도
                os.remove(file_path)
                return True
            except (OSError, FileNotFoundError, PermissionError) as e: # PermissionError 추가
                if attempt < max_retries - 1:
                    self.logger.debug(f"삭제 재시도 {attempt + 1}/{max_retries}: {os.path.basename(file_path)} - {str(e)}")
                    # 중지 신호 대기 (응답성 향상)
                    if self.stop_event.wait(timeout=retry_delay):
                        return False
                    continue
                else:
                    self.logger.warning(f"파일 삭제 실패: {os.path.basename(file_path)} - {str(e)}")
                    return False
        return False

    def _is_file_locked(self, file_path):
        """파일이 다른 프로세스에서 사용 중인지 확인 (사용되지 않음)"""
        # 이 함수는 더 이상 사용되지 않지만, 호환성을 위해 남겨둘 수 있습니다.
        # 실제로는 _safe_delete_file의 예외 처리로 대체되었습니다.
        return False


class ScreenCapture:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("화면 모니터링")
        self.root.geometry("450x950")  # 높이 증가 (이미지 설정 영역 추가)
        self.root.resizable(False, False)
        
        # 로깅 설정
        self.setup_logging()
        
        # 캡처 상태 관리
        self.is_capturing = False
        self.capture_thread = None
        
        # 캡처 간격 설정 (초)
        self.capture_interval = tk.DoubleVar(value=2.0)
        
        # 자동 삭제 설정 (Rolling Cleanup) - 10분마다 설정된 시간이 지난 파일 삭제
        self.rolling_cleanup_enabled = tk.BooleanVar(value=True)
        self.rolling_cleanup_age_value = tk.DoubleVar(value=24.0) # 삭제 주기 값 (기본 24시간)
        self.rolling_cleanup_age_unit = tk.StringVar(value="시간") # 삭제 주기 단위

        # 유효성 검사 진행 상태 플래그 (팝업 중복 방지)
        self._validation_in_progress = False
        self.rolling_cleanup = None  # RollingCleanup 인스턴스
        self.cleanup_timer_job = None # 타이머 작업을 위한 변수
        
        # 프로그램 리소스 모니터링
        self.resource_monitor_enabled = tk.BooleanVar(value=True)
        self.resource_monitor_thread = None
        self.current_process = None

        # 파일 시스템 작업 동기화를 위한 락
        self.file_lock = threading.Lock()

        # 스레드 종료 신호
        self.stop_event = threading.Event()

        # 시스템 트레이 관련 변수
        self.tray_icon = None
        self.tray_thread = None

        # 이미지 설정
        self.image_format = tk.StringVar(value="JPEG")  # "JPEG" 또는 "WEBP"
        self.image_quality = tk.IntVar(value=15)  # 1-100
        self.image_quality_value = tk.DoubleVar(value=15.0)  # Scale용 실수 값
        self.image_resolution = tk.StringVar(value="원본")  # 해상도 설정
        self.image_grayscale = tk.BooleanVar(value=False)  # 흑백 변환 설정

        # 저장 폴더 설정
        self.save_folder = "screenshots"
        if not os.path.exists(self.save_folder):
            os.makedirs(self.save_folder)

        self.setup_ui()

        # 리소스 모니터링 시작
        self.start_resource_monitoring()

        # 시스템 트레이 초기화
        self.setup_system_tray()

        # GUI가 완전히 로드된 후 자동으로 캡처 시작
        self.root.after(100, self.start_capture_automatically)
    
    def setup_logging(self):
        """날짜별 로깅 시스템 설정"""
        # 현재 날짜로 로그 파일 경로 생성 (logs/년/월/일.log)
        now = datetime.now()
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")

        # 로그 폴더 경로 생성
        log_dir = os.path.join("logs", year, month)
        log_filename = os.path.join(log_dir, f"{day}.log")

        # 로그 폴더가 없으면 생성
        os.makedirs(log_dir, exist_ok=True)

        # 날짜별 로그 핸들러 클래스
        class DateRotatingFileHandler(logging.FileHandler):
            def __init__(self, filename, encoding=None):
                self.base_filename = filename
                self.current_date = self._get_current_date()
                super().__init__(self._get_full_filename(), encoding=encoding)

            def _get_current_date(self):
                now = datetime.now()
                return now.strftime("%Y%m%d")

            def _get_full_filename(self):
                now = datetime.now()
                year = now.strftime("%Y")
                month = now.strftime("%m")
                day = now.strftime("%d")
                log_dir = os.path.join("logs", year, month)
                os.makedirs(log_dir, exist_ok=True)
                return os.path.join(log_dir, f"{day}.log")

            def emit(self, record):
                # 날짜가 바뀌었는지 확인
                current_date = self._get_current_date()
                if current_date != self.current_date:
                    # 날짜가 바뀌었으면 파일 핸들러 교체
                    self.current_date = current_date
                    self.close()
                    self.baseFilename = self._get_full_filename()
                    self.stream = open(self.baseFilename, self.mode, encoding=self.encoding)
                super().emit(record)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                DateRotatingFileHandler(log_filename, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("프로그램 시작됨")
    
    def is_file_locked(self, file_path):
        """파일이 다른 프로세스에서 사용 중인지 확인"""
        try:
            # 파일을 배타적 모드로 열어보기
            with open(file_path, 'r+b') as f:
                return False
        except (IOError, OSError):
            return True
    
    def safe_delete_file(self, file_path, max_retries=3, retry_delay=1):
        """안전한 파일 삭제 (재시도 로직 포함)"""
        for attempt in range(max_retries):
            try:
                # 파일 잠금 상태 확인
                if self.is_file_locked(file_path):
                    if attempt < max_retries - 1:
                        self.logger.warning(f"파일이 사용 중입니다. 재시도 {attempt + 1}/{max_retries}: {file_path}")
                        time.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"파일 삭제 실패 (최대 재시도 초과): {file_path}")
                        return False
                
                # 파일 삭제 시도
                os.remove(file_path)
                self.logger.info(f"파일 삭제 성공: {file_path}")
                return True
                
            except FileNotFoundError:
                self.logger.warning(f"파일이 이미 존재하지 않음: {file_path}")
                return True  # 이미 삭제된 상태로 간주
            except PermissionError:
                if attempt < max_retries - 1:
                    self.logger.warning(f"권한 오류로 재시도 {attempt + 1}/{max_retries}: {file_path}")
                    time.sleep(retry_delay)
                    continue
                else:
                    self.logger.error(f"권한 오류로 파일 삭제 실패: {file_path}")
                    return False
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"예상치 못한 오류로 재시도 {attempt + 1}/{max_retries}: {file_path} - {str(e)}")
                    time.sleep(retry_delay)
                    continue
                else:
                    self.logger.error(f"파일 삭제 실패: {file_path} - {str(e)}")
                    return False
        
        return False
    
    def setup_ui(self):
        """GUI 인터페이스 설정"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 제목
        title_label = ttk.Label(main_frame, text="화면 모니터링", 
                               font=("Arial", 14, "bold"))
        title_label.pack(pady=10)
        
        # 캡처 간격 설정 프레임
        interval_frame = ttk.LabelFrame(main_frame, text="캡처 간격 설정", padding="8")
        interval_frame.pack(fill=tk.X, pady=8)
        
        # 간격 입력 프레임
        input_frame = ttk.Frame(interval_frame)
        input_frame.pack(fill=tk.X, pady=5)
        
        # 간격 입력 라벨
        ttk.Label(input_frame, text="간격:").pack(side=tk.LEFT, padx=(0, 5))
        
        # 간격 입력 필드
        self.interval_entry = ttk.Entry(input_frame, width=10, textvariable=self.capture_interval)
        self.interval_entry.pack(side=tk.LEFT, padx=5)
        self.interval_entry.bind('<KeyRelease>', self.validate_interval)
        
        # 초 라벨
        ttk.Label(input_frame, text="초").pack(side=tk.LEFT, padx=(5, 0))
        
        # 적용 버튼
        self.apply_button = ttk.Button(input_frame, text="적용", command=self.apply_interval)
        self.apply_button.pack(side=tk.LEFT, padx=(10, 0))
        
        # 간격 안내 라벨
        self.interval_info = ttk.Label(interval_frame, text="범위: 0.1 ~ 3600초 (1시간)", 
                                      font=("Arial", 8), foreground="gray")
        self.interval_info.pack(pady=(5, 0))
        
        # 저장 경로 설정 프레임
        path_frame = ttk.LabelFrame(main_frame, text="저장 경로 설정", padding="8")
        path_frame.pack(fill=tk.X, pady=8)
        
        # 경로 표시 및 선택 프레임
        path_control_frame = ttk.Frame(path_frame)
        path_control_frame.pack(fill=tk.X, pady=5)
        
        # 현재 경로 표시
        self.path_label = ttk.Label(path_control_frame, text=f"현재 경로: {os.path.abspath(self.save_folder)}", 
                                   font=("Arial", 9), foreground="blue")
        self.path_label.pack(anchor=tk.W, pady=(0, 5))
        
        # 경로 선택 버튼
        self.path_button = ttk.Button(path_control_frame, text="경로 변경", 
                                     command=self.select_save_path)
        self.path_button.pack(side=tk.LEFT)
        
        # 자동 삭제 설정 프레임
        cleanup_frame = ttk.LabelFrame(main_frame, text="자동 삭제 설정", padding="8")
        cleanup_frame.pack(fill=tk.X, pady=8)

        # 자동 삭제 활성화 체크박스
        self.cleanup_checkbox = ttk.Checkbutton(cleanup_frame, text=" 삭제 활성화 (지정된 시간보다 오래된 파일 자동 삭제)",
                                               variable=self.rolling_cleanup_enabled,
                                               command=self.toggle_cleanup_settings)
        self.cleanup_checkbox.pack(anchor=tk.W, pady=(0, 5))

        # 삭제 주기 설정 프레임
        cleanup_interval_frame = ttk.Frame(cleanup_frame)
        cleanup_interval_frame.pack(fill=tk.X, pady=5, padx=5)

        ttk.Label(cleanup_interval_frame, text="지정 시간:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.cleanup_age_entry = ttk.Entry(cleanup_interval_frame, width=8, textvariable=self.rolling_cleanup_age_value)
        self.cleanup_age_entry.pack(side=tk.LEFT, padx=5)
        self.cleanup_age_entry.bind('<KeyRelease>', self.apply_cleanup_settings_immediately)

        self.cleanup_unit_combo = ttk.Combobox(cleanup_interval_frame, textvariable=self.rolling_cleanup_age_unit,
                                              values=["분", "시간"], state="readonly", width=5)
        self.cleanup_unit_combo.pack(side=tk.LEFT, padx=(0, 5))
        self.cleanup_unit_combo.bind('<<ComboboxSelected>>', self.apply_cleanup_settings_immediately)

        # 경고 메시지 표시 레이블 (초기에는 보이지 않음)
        self.cleanup_warning_label = ttk.Label(cleanup_frame, text="", font=("Arial", 9), foreground="red")
        self.cleanup_warning_label.pack(pady=(2, 0))

        # 삭제 주기 정보 라벨 (기본값 표시)
        default_cleanup_age_value = self.rolling_cleanup_age_value.get()
        default_unit = self.rolling_cleanup_age_unit.get()
        self.cleanup_info = ttk.Label(cleanup_frame, text=f"{default_cleanup_age_value}{default_unit}을 초과한 파일을 10분마다 삭제",
                                     font=("Arial", 9), foreground="blue")
        self.cleanup_info.pack(pady=(5, 0))

        # 다음 정리 시간 표시 라벨
        self.next_cleanup_label = ttk.Label(cleanup_frame, text="다음 삭제까지: --",
                                           font=("Arial", 9, "bold"), foreground="darkblue")
        self.next_cleanup_label.pack(pady=(5, 0))
        
        # 프로그램 리소스 모니터링 프레임
        resource_frame = ttk.LabelFrame(main_frame, text="프로그램 리소스 모니터링", padding="8")
        resource_frame.pack(fill=tk.X, pady=8)
        
        # 리소스 모니터링 활성화 체크박스
        self.resource_checkbox = ttk.Checkbutton(resource_frame, text="프로그램 리소스 모니터링 활성화", 
                                               variable=self.resource_monitor_enabled,
                                               command=self.toggle_resource_monitoring)
        self.resource_checkbox.pack(anchor=tk.W, pady=(0, 5))
        
        # 리소스 정보 표시 프레임
        resource_info_frame = ttk.Frame(resource_frame)
        resource_info_frame.pack(fill=tk.X, pady=5)
        
        # CPU 사용률 표시
        self.cpu_label = ttk.Label(resource_info_frame, text="CPU: --", 
                                  font=("Arial", 9), foreground="blue")
        self.cpu_label.pack(side=tk.LEFT, padx=(0, 20))
        
        # 메모리 사용량 표시
        self.memory_label = ttk.Label(resource_info_frame, text="메모리: --", 
                                     font=("Arial", 9), foreground="blue")
        self.memory_label.pack(side=tk.LEFT, padx=(0, 20))
        
        # 폴더 크기 표시
        self.disk_label = ttk.Label(resource_info_frame, text="폴더크기: --", 
                                   font=("Arial", 9), foreground="blue")
        self.disk_label.pack(side=tk.LEFT)
        
        # 이미지 설정 프레임
        image_frame = ttk.LabelFrame(main_frame, text="이미지 설정", padding="8")
        image_frame.pack(fill=tk.X, pady=8)

        # 포맷 선택 프레임
        format_frame = ttk.Frame(image_frame)
        format_frame.pack(fill=tk.X, pady=5)

        ttk.Label(format_frame, text="포맷:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Radiobutton(format_frame, text="JPEG", variable=self.image_format, value="JPEG").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(format_frame, text="WebP", variable=self.image_format, value="WEBP").pack(side=tk.LEFT)

        # 품질 설정 프레임
        quality_frame = ttk.Frame(image_frame)
        quality_frame.pack(fill=tk.X, pady=5)

        ttk.Label(quality_frame, text="품질:").pack(side=tk.LEFT, padx=(0, 5))
        quality_scale = ttk.Scale(quality_frame, from_=1, to=100, orient=tk.HORIZONTAL,
                                 variable=self.image_quality_value, command=self.update_quality_display)
        quality_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        quality_label = ttk.Label(quality_frame, textvariable=self.image_quality)
        quality_label.pack(side=tk.LEFT, padx=(5, 0))

        # 해상도 설정 프레임
        resolution_frame = ttk.Frame(image_frame)
        resolution_frame.pack(fill=tk.X, pady=5)

        ttk.Label(resolution_frame, text="해상도:").pack(side=tk.LEFT, padx=(0, 5))
        resolutions = ["원본", "1920x1080", "1280x720", "1024x768", "800x600"]
        resolution_combo = ttk.Combobox(resolution_frame, textvariable=self.image_resolution,
                                       values=resolutions, state="readonly", width=12)
        resolution_combo.pack(side=tk.LEFT, padx=(0, 5))

        # 흑백 변환 설정 프레임
        grayscale_frame = ttk.Frame(image_frame)
        grayscale_frame.pack(fill=tk.X, pady=5)

        self.grayscale_checkbox = ttk.Checkbutton(grayscale_frame, text="흑백 변환",
                                                 variable=self.image_grayscale)
        self.grayscale_checkbox.pack(anchor=tk.W)

        # 상태 표시
        self.status_label = ttk.Label(main_frame, text="대기 중...",
                                     font=("Arial", 10))
        self.status_label.pack(pady=5)
        
        # 캡처된 이미지 수 표시
        self.count_label = ttk.Label(main_frame, text="캡처된 이미지: 0개", 
                                    font=("Arial", 9))
        self.count_label.pack(pady=5)
        
        # 버튼 프레임
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=20, fill=tk.X)
        
        # 시작/정지 버튼 (더 크고 눈에 띄게)
        self.start_button = ttk.Button(button_frame, text="캡처 시작", 
                                      command=self.toggle_capture,
                                      width=20)
        self.start_button.pack(side=tk.LEFT, padx=(30, 15), pady=10, ipady=10)
        
        # 종료 버튼 (더 크고 눈에 띄게)
        self.quit_button = ttk.Button(button_frame, text="프로그램 종료", 
                                     command=self.quit_program,
                                     width=20)
        self.quit_button.pack(side=tk.RIGHT, padx=(15, 30), pady=10, ipady=10)
        
        # 창 닫기 이벤트 처리
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

    def update_quality_display(self, value=None):
        """품질 값이 변경될 때 정수로 변환하여 표시"""
        try:
            if value is None:
                # 직접 호출된 경우 현재 Scale 값 사용
                current_value = self.image_quality_value.get()
            else:
                # 콜백에서 호출된 경우 파라미터 값 사용
                current_value = float(value)

            # 실수 값을 정수로 변환
            int_value = int(current_value)
            self.image_quality.set(int_value)
        except (ValueError, TypeError):
            # 변환 실패 시 현재 값 유지
            pass

    def create_tray_menu(self):
        """동적으로 트레이 메뉴 생성"""
        menu_items = []

        # 화면 모니터링 표시 메뉴 (항상 활성화)
        menu_items.append(item('화면 모니터링 표시', self.show_window))

        # 모니터링 시작 메뉴 (캡처 중이 아닐 때만 활성화)
        if not self.is_capturing:
            menu_items.append(item('▶ 모니터링 시작', self.start_capture_from_tray))
        else:
            # 캡처 중일 때는 상태 표시 (클릭 불가)
            menu_items.append(item('⏸ 모니터링 시작 (실행 중)', None))

        # 모니터링 정지 메뉴 (캡처 중일 때만 활성화)
        if self.is_capturing:
            menu_items.append(item('⏹ 모니터링 정지', self.stop_capture_from_tray))
        else:
            # 캡처 중이 아닐 때는 상태 표시 (클릭 불가)
            menu_items.append(item('⏸ 모니터링 정지 (정지됨)', None))

        # 종료 메뉴 (항상 활성화)
        menu_items.append(item('종료', self.quit_program))

        # pystray.Menu 객체로 반환
        return Menu(*menu_items)

    def update_tray_menu(self):
        """트레이 메뉴 업데이트"""
        try:
            if self.tray_icon:
                # 새로운 메뉴로 설정
                new_menu = self.create_tray_menu()
                self.tray_icon.menu = new_menu
        except Exception as e:
            self.logger.error(f"트레이 메뉴 업데이트 실패: {str(e)}")

    def setup_system_tray(self):
        """시스템 트레이 설정"""
        try:
            # 트레이 아이콘 이미지 생성 (단색 아이콘)
            icon_image = Image.new('RGB', (64, 64), color=(0, 123, 255))
            draw = ImageDraw.Draw(icon_image)
            # 간단한 카메라 아이콘 모양 그리기
            draw.rectangle([16, 20, 48, 44], fill=(255, 255, 255))
            draw.rectangle([20, 16, 44, 20], fill=(255, 255, 255))
            draw.ellipse([22, 26, 30, 34], fill=(0, 0, 0))

            # 시스템 트레이 아이콘 생성 (초기 메뉴 설정)
            self.tray_icon = pystray.Icon(
                "screen_capture",
                icon_image,
                "화면 모니터링",
                self.create_tray_menu()
            )

            # 트레이 아이콘을 별도 스레드에서 실행
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self.tray_thread.start()

        except Exception as e:
            self.logger.error(f"시스템 트레이 설정 실패: {str(e)}")

    def show_window(self):
        """GUI 창 표시"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        """GUI 창 숨김"""
        self.root.withdraw()

    def start_capture_from_tray(self):
        """트레이 메뉴에서 모니터링 시작"""
        if not self.is_capturing:
            self.start_capture_automatically()
            # 트레이 메뉴 업데이트
            self.update_tray_menu()

    def stop_capture_from_tray(self):
        """트레이 메뉴에서 모니터링 정지"""
        if self.is_capturing:
            self.toggle_capture()
            # 트레이 메뉴 업데이트
            self.update_tray_menu()
    
    def validate_interval(self, event=None):
        """간격 입력값 실시간 검증"""
        try:
            value = self.interval_entry.get()
            if value:  # 빈 문자열이 아닌 경우에만 검증
                interval = float(value)
                if 0.1 <= interval <= 3600:
                    self.interval_entry.config(foreground="black")
                    self.interval_info.config(text="범위: 0.1 ~ 3600초 (1시간)", foreground="gray")
                else:
                    self.interval_entry.config(foreground="red")
                    self.interval_info.config(text="❌ 범위를 벗어남: 0.1 ~ 3600초", foreground="red")
        except ValueError:
            if self.interval_entry.get():  # 빈 문자열이 아닌 경우에만 에러 표시
                self.interval_entry.config(foreground="red")
                self.interval_info.config(text="❌ 숫자만 입력하세요", foreground="red")
    
    def apply_interval(self):
        """간격 설정 적용"""
        try:
            value = float(self.interval_entry.get())
            if 0.1 <= value <= 3600:
                self.capture_interval.set(value)
                self.interval_info.config(text=f"✅ {value}초로 설정됨", foreground="green")
                self.root.after(2000, lambda: self.interval_info.config(
                    text="범위: 0.1 ~ 3600초 (1시간)", foreground="gray"))
            else:
                self.interval_info.config(text="❌ 범위를 벗어남: 0.1 ~ 3600초", foreground="red")
        except ValueError:
            self.interval_info.config(text="❌ 올바른 숫자를 입력하세요", foreground="red")
    
    def select_save_path(self):
        """저장 경로 선택"""
        new_path = filedialog.askdirectory(
            title="스크린샷 저장 경로 선택",
            initialdir=self.save_folder
        )
        
        if new_path:  # 사용자가 경로를 선택한 경우
            try:
                # 선택한 경로가 존재하지 않으면 생성
                if not os.path.exists(new_path):
                    os.makedirs(new_path)
                
                # 경로 업데이트
                self.save_folder = new_path
                self.update_path_label()
                
                # 성공 메시지
                messagebox.showinfo("경로 변경 완료", f"저장 경로가 변경되었습니다:\n{new_path}")
                
            except Exception as e:
                messagebox.showerror("경로 변경 실패", f"경로를 변경할 수 없습니다:\n{str(e)}")
    

    def update_path_label(self):
        """경로 라벨 업데이트"""
        abs_path = os.path.abspath(self.save_folder)
        # 경로가 너무 길면 줄임
        if len(abs_path) > 50:
            display_path = "..." + abs_path[-47:]
        else:
            display_path = abs_path
        
        self.path_label.config(text=f"현재 경로: {display_path}")
    
    def validate_cleanup_interval(self):
        """자동 삭제 주기 유효성 검사"""
        try:
            # Tkinter DoubleVar에서 가져온 값 처리 (숫자나 문자열일 수 있음)
            raw_value = self.rolling_cleanup_age_value.get()

            # 숫자 타입이면 문자열로 변환
            if isinstance(raw_value, (int, float)):
                value_str = str(raw_value)
            else:
                value_str = raw_value

            unit = self.rolling_cleanup_age_unit.get()

            # 빈 문자열이나 공백만 있는 경우 체크
            if not value_str or (isinstance(value_str, str) and value_str.strip() == ""):
                self.show_cleanup_warning("삭제 주기를 입력해주세요. 숫자만 입력 가능합니다.")
                return False

            # 문자열을 실수로 변환
            try:
                value = float(value_str)
            except (ValueError, TypeError):
                self.show_cleanup_warning("유효한 숫자를 입력해주세요. 예: 30, 2.5")
                return False

            # 값이 0이거나 음수인 경우 체크
            if value <= 0:
                self.show_cleanup_warning("삭제 주기는 0보다 큰 값을 입력해주세요.")
                return False

            if unit == "분":
                # 1분 ~ 60분 (1시간)
                if not (1 <= value <= 60):
                    self.show_cleanup_warning("삭제 주기는 1분에서 60분(1시간) 사이로 설정해야 합니다.")
                    return False
            elif unit == "시간":
                # 1시간 ~ 525600시간(365일)
                if not (1 <= value <= 525600):
                    self.show_cleanup_warning("삭제 주기는 1시간에서 525600시간(365일) 사이로 설정해야 합니다.")
                    return False

            # 유효성 검사 통과 시 경고 메시지 제거
            self.clear_cleanup_warning()
            return True
        except Exception as e:
            # Tkinter TclError 등 모든 예외 처리
            if "_tkinter.TclError" in str(type(e)) or "expected floating-point number" in str(e):
                self.show_cleanup_warning("삭제 주기를 입력해주세요. 숫자만 입력 가능합니다.")
            else:
                self.show_cleanup_warning(f"입력값 오류: {str(e)}")
            return False

    def show_cleanup_warning(self, message):
        """삭제 주기 경고 메시지 표시"""
        if hasattr(self, 'cleanup_warning_label'):
            self.cleanup_warning_label.config(text=message)

    def clear_cleanup_warning(self):
        """삭제 주기 경고 메시지 제거"""
        if hasattr(self, 'cleanup_warning_label'):
            self.cleanup_warning_label.config(text="")

    def apply_cleanup_settings_immediately(self, event=None):
        """시간/단위 변경 시 자동 삭제 설정 즉시 적용"""
        # 자동 삭제가 활성화되어 있지 않으면 무시
        if not self.rolling_cleanup_enabled.get():
            return

        # 이벤트 반복 호출 방지를 위한 플래그
        if hasattr(self, '_validation_in_progress') and self._validation_in_progress:
            return

        # 유효성 검사 진행 중 플래그 설정
        self._validation_in_progress = True

        try:
            # 유효성 검사
            if not self.validate_cleanup_interval():
                return
        finally:
            # 유효성 검사 완료 후 플래그 해제
            self._validation_in_progress = False

        # 사용자가 입력한 값을 초 단위로 변환
        try:
            cleanup_age_value = self.rolling_cleanup_age_value.get()
            unit = self.rolling_cleanup_age_unit.get()
            if unit == "분":
                cleanup_age_seconds = cleanup_age_value * 60
            else:  # "시간"
                cleanup_age_seconds = cleanup_age_value * 3600
        except ValueError:
            return

        # RollingCleanup 인스턴스 생성 또는 업데이트
        if self.rolling_cleanup is None:
            self.rolling_cleanup = RollingCleanup(
                self.save_folder,
                self.logger,
                self.stop_event,
                cleanup_age_seconds
            )
            self.rolling_cleanup.start()
        else:
            self.rolling_cleanup.update_cleanup_age(cleanup_age_seconds)

        # UI 텍스트 업데이트
        self.cleanup_info.config(text=f"{cleanup_age_value}{unit}을 초과한 파일을 10분마다 삭제", foreground="blue")

    def toggle_cleanup_settings(self):
        """자동 삭제 설정 토글"""
        if self.rolling_cleanup_enabled.get():
            # 유효성 검사
            if not self.validate_cleanup_interval():
                self.rolling_cleanup_enabled.set(False) # 체크박스 해제
                return

            # 사용자가 입력한 값을 초 단위로 변환
            try:
                cleanup_age_value = self.rolling_cleanup_age_value.get()
                unit = self.rolling_cleanup_age_unit.get()
                if unit == "분":
                    cleanup_age_seconds = cleanup_age_value * 60
                else:  # "시간"
                    cleanup_age_seconds = cleanup_age_value * 3600
            except ValueError:
                messagebox.showerror("오류", "삭제 주기 값을 확인하세요.")
                self.rolling_cleanup_enabled.set(False)
                return

            # RollingCleanup 인스턴스 생성 또는 업데이트
            if self.rolling_cleanup is None:
                self.rolling_cleanup = RollingCleanup(
                    self.save_folder,
                    self.logger,
                    self.stop_event,
                    cleanup_age_seconds
                )
                self.rolling_cleanup.start()
            else:
                self.rolling_cleanup.update_cleanup_age(cleanup_age_seconds)

            self.cleanup_info.config(text=f"{cleanup_age_value}{unit}을 초과한 파일을 10분마다 삭제", foreground="blue")
            # self.start_cleanup_timer() # -> 캡처 시작 버튼으로 이동

        else:
            # RollingCleanup 인스턴스 중지 및 정리
            if self.rolling_cleanup is not None:
                self.rolling_cleanup.stop()
                self.rolling_cleanup = None
            self.cleanup_info.config(text="자동 삭제가 비활성화되었습니다", foreground="gray")
            self.next_cleanup_label.config(text="다음 삭제까지: --")
            self.stop_cleanup_timer()
    
    
    def start_cleanup_timer(self):
        """10분 삭제 주기 타이머 시작"""
        # 기존 타이머가 있다면 취소
        if self.cleanup_timer_job:
            self.root.after_cancel(self.cleanup_timer_job)
        
        # 10분(600초) 타이머 시작
        self.update_cleanup_timer(600)

    def stop_cleanup_timer(self):
        """삭제 주기 타이머 중지"""
        if self.cleanup_timer_job:
            self.root.after_cancel(self.cleanup_timer_job)
            self.cleanup_timer_job = None
        self.next_cleanup_label.config(text="다음 삭제까지: --")

    def update_cleanup_timer(self, remaining_seconds):
        """타이머 라벨을 1초마다 업데이트"""
        if remaining_seconds > 0:
            mins, secs = divmod(remaining_seconds, 60)
            timer_text = f"다음 삭제까지: {mins:02d}분 {secs:02d}초"
            self.next_cleanup_label.config(text=timer_text)
            
            # 1초 후에 다시 이 함수를 호출
            self.cleanup_timer_job = self.root.after(1000, self.update_cleanup_timer, remaining_seconds - 1)
        else:
            self.next_cleanup_label.config(text="삭제 작업 실행 중...")
            
            # 삭제 작업이 끝난 후 다시 타이머 시작 (약 5초 후)
            self.cleanup_timer_job = self.root.after(5000, self.start_cleanup_timer)

    def toggle_resource_monitoring(self):
        """리소스 모니터링 토글"""
        if self.resource_monitor_enabled.get():
            self.start_resource_monitoring()
        else:
            self.stop_resource_monitoring()
    
    def start_resource_monitoring(self):
        """프로그램 리소스 모니터링 시작"""
        if not self.resource_monitor_enabled.get():
            return
        
        # 현재 프로세스 정보 초기화
        try:
            self.current_process = psutil.Process(os.getpid())
            self.logger.info(f"프로그램 PID: {os.getpid()}")
        except Exception as e:
            self.logger.error(f"프로세스 정보 가져오기 실패: {str(e)}")
            return
        
        if self.resource_monitor_thread is None or not self.resource_monitor_thread.is_alive():
            self.resource_monitor_thread = threading.Thread(target=self.resource_monitor_worker, daemon=True)
            self.resource_monitor_thread.start()
            self.logger.info("프로그램 리소스 모니터링 시작")
    
    def stop_resource_monitoring(self):
        """프로그램 리소스 모니터링 중지"""
        self.resource_monitor_enabled.set(False)
        self.root.after(0, self.clear_resource_display)
        self.logger.info("프로그램 리소스 모니터링 중지")
    
    def resource_monitor_worker(self):
        """프로그램 리소스 모니터링 작업자"""
        last_folder_size_check_time = 0
        folder_size_mb = 0
        folder_size_check_interval = 30  # 30초

        while self.resource_monitor_enabled.get() and not self.stop_event.is_set():
            try:
                if self.current_process is None:
                    # 프로세스가 없으면 재초기화 시도
                    try:
                        self.current_process = psutil.Process(os.getpid())
                    except Exception as e:
                        self.logger.error(f"프로세스 재초기화 실패: {str(e)}")
                        time.sleep(5)
                        continue
                
                # 현재 프로그램의 CPU 사용률 (1초 간격으로 측정)
                cpu_percent = self.current_process.cpu_percent(interval=1)
                
                # 현재 프로그램의 메모리 사용량
                memory_info = self.current_process.memory_info()
                memory_mb = memory_info.rss / (1024 * 1024)  # RSS(물리 메모리)를 MB로 변환
                
                # 스크린샷 폴더 크기 계산 (30초마다)
                current_time = time.time()
                if current_time - last_folder_size_check_time > folder_size_check_interval:
                    folder_size_mb = self.get_folder_size_mb(self.save_folder)
                    last_folder_size_check_time = current_time
                
                # GUI 업데이트
                self.root.after(0, self.update_resource_display, cpu_percent, memory_mb, folder_size_mb)
                
                # 1초 대기 (CPU 측정에서 이미 1초 간격 있으므로 추가 대기 없음)
                
            except psutil.NoSuchProcess:
                self.logger.error("프로세스가 존재하지 않습니다")
                break
            except psutil.AccessDenied:
                self.logger.error("프로세스 접근이 거부되었습니다")
                time.sleep(5)
            except Exception as e:
                self.logger.error(f"프로그램 리소스 모니터링 오류: {str(e)}")
                time.sleep(5)  # 오류 발생 시 5초 대기 후 재시도
    
    def get_folder_size_mb(self, folder_path):
        """폴더 크기를 MB 단위로 계산 (os.scandir 사용)"""
        total_size = 0

        def calculate_size_recursive(path):
            nonlocal total_size
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        try:
                            if entry.is_file():
                                total_size += entry.stat().st_size
                            elif entry.is_dir():
                                calculate_size_recursive(entry.path)
                        except (OSError, FileNotFoundError):
                            # 파일이 삭제되었거나 접근할 수 없는 경우 무시
                            pass
            except (PermissionError, FileNotFoundError):
                # 폴더에 접근할 수 없는 경우 무시
                pass

        try:
            calculate_size_recursive(folder_path)
        except Exception as e:
            self.logger.warning(f"폴더 크기 계산 오류: {str(e)}")
            return 0

        return total_size / (1024 * 1024)  # 바이트를 MB로 변환
    
    def update_resource_display(self, cpu_percent, memory_mb, folder_size_mb):
        """프로그램 리소스 사용률 표시 업데이트"""
        if not self.resource_monitor_enabled.get():
            return
        
        # CPU 색상 설정 (프로그램 CPU 사용률)
        cpu_color = "red" if cpu_percent > 50 else "orange" if cpu_percent > 20 else "blue"
        self.cpu_label.config(text=f"CPU: {cpu_percent:.1f}%", foreground=cpu_color)
        
        # 메모리 색상 설정 (프로그램 메모리 사용량)
        memory_color = "red" if memory_mb > 500 else "orange" if memory_mb > 200 else "blue"
        self.memory_label.config(text=f"메모리: {memory_mb:.1f}MB", foreground=memory_color)
        
        # 폴더 크기 색상 설정
        if folder_size_mb > 1000:  # 1GB 이상
            folder_color = "red"
            folder_text = f"폴더크기: {folder_size_mb/1024:.1f}GB"
        elif folder_size_mb > 500:  # 500MB 이상
            folder_color = "orange"
            folder_text = f"폴더크기: {folder_size_mb:.1f}MB"
        else:
            folder_color = "blue"
            folder_text = f"폴더크기: {folder_size_mb:.1f}MB"
        
        self.disk_label.config(text=folder_text, foreground=folder_color)
    
    def clear_resource_display(self):
        """프로그램 리소스 표시 초기화"""
        self.cpu_label.config(text="CPU: --", foreground="gray")
        self.memory_label.config(text="메모리: --", foreground="gray")
        self.disk_label.config(text="폴더크기: --", foreground="gray")
    
        
    def add_timestamp_overlay(self, image):
        """이미지에 현재 시간 오버레이 추가"""
        # 이미지 복사본 생성
        img_with_text = image.copy()
        draw = ImageDraw.Draw(img_with_text)
        
        # 현재 시간 텍스트 생성
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 폰트 설정 (시스템 기본 폰트 사용)
        try:
            # Windows 기본 폰트 시도
            font = ImageFont.truetype("arial.ttf", 24)
        except:
            try:
                # 다른 폰트 시도
                font = ImageFont.truetype("malgun.ttf", 24)
            except:
                # 기본 폰트 사용
                font = ImageFont.load_default()
        
        # 텍스트 크기 계산
        bbox = draw.textbbox((0, 0), current_time, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # 배경 사각형 좌표 (패딩 포함)
        padding = 10
        x1, y1 = 10, 10
        x2, y2 = x1 + text_width + (padding * 2), y1 + text_height + (padding * 2)
        
        # 반투명 검은색 배경 그리기
        draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0, 180))
        
        # 흰색 텍스트 그리기
        text_x = x1 + padding
        text_y = y1 + padding
        draw.text((text_x, text_y), current_time, fill=(255, 255, 255), font=font)
        
        return img_with_text
    
    def get_system_status(self):
        """시스템 상태 정보 수집"""
        try:
            # 메모리 정보
            import psutil
            memory = psutil.virtual_memory()
            memory_info = f"메모리: {memory.percent:.1f}% 사용 ({memory.available//1024//1024}MB 사용가능)"

            # 디스크 정보
            disk = psutil.disk_usage(self.save_folder)
            disk_info = f"디스크: {disk.percent:.1f}% 사용 ({disk.free//1024//1024//1024}GB 사용가능)"

            return f"{memory_info}, {disk_info}"
        except:
            return "시스템 상태 확인 불가"

    def capture_screen(self):
        """화면 모니터링 함수"""
        capture_count = 0
        
        while self.is_capturing and not self.stop_event.is_set():
            try:
                # 전체 화면 모니터링 (PIL 에러 처리 강화)
                try:
                    screenshot = ImageGrab.grab()
                except (OSError, RuntimeError) as pil_error:
                    # PIL 라이브러리 관련 시스템 에러
                    error_msg = f"PIL 화면 캡처 실패: {str(pil_error)}"
                    self.logger.error(error_msg)
                    self.root.after(0, self.update_status, f"PIL 캡처 에러 - 잠시 후 재시도", capture_count)
                    time.sleep(3)  # PIL 에러는 더 긴 대기 시간
                    continue
                
                # 현재 시간 오버레이 추가
                screenshot_with_time = self.add_timestamp_overlay(screenshot)

                # 해상도 조정 적용 (thumbnail 사용)
                if self.image_resolution.get() != "원본":
                    width, height = map(int, self.image_resolution.get().split('x'))
                    screenshot_with_time.thumbnail((width, height), Image.LANCZOS)

                # 흑백 변환 적용 (PIL 메모리 최적화)
                if self.image_grayscale.get():
                    try:
                        screenshot_with_time = screenshot_with_time.convert('L')
                    except MemoryError:
                        self.logger.warning("메모리 부족으로 흑백 변환 실패 - 원본 유지")
                        # 메모리 부족 시 흑백 변환 생략
                    except Exception as e:
                        self.logger.error(f"흑백 변환 중 PIL 에러: {str(e)}")
                        # 변환 실패 시 원본 유지

                # 파일명 생성 (타임스탬프 포함)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 밀리초까지
                format_ext = "webp" if self.image_format.get() == "WEBP" else "jpg"
                filename = f"screenshot_{timestamp}.{format_ext}"
                filepath = os.path.join(self.save_folder, filename)

                # 파일 저장 시 락 획득 (경쟁 상태 방지)
                with self.file_lock:
                    # 선택된 포맷과 품질로 저장
                    quality_value = int(self.image_quality_value.get())
                    if self.image_format.get() == "WEBP":
                        screenshot_with_time.save(filepath, "WEBP", quality=quality_value)
                    else:
                        screenshot_with_time.save(filepath, "JPEG", quality=quality_value, optimize=True)
                
                capture_count += 1
                
                # GUI 업데이트 (메인 스레드에서 실행)
                self.root.after(0, self.update_status, 
                               f"캡처 중... ({capture_count}번째)", capture_count)
                
                # 설정된 간격만큼 대기
                interval = self.capture_interval.get()
                time.sleep(interval)
                
            except OSError as e:
                # 시스템 리소스 관련 에러
                system_status = self.get_system_status()
                error_msg = f"시스템 리소스 오류: {str(e)} [{system_status}]"
                self.logger.error(error_msg)
                self.root.after(0, self.update_status, "시스템 리소스 오류 발생 - 잠시 후 재시도", capture_count)
                time.sleep(2)  # 잠시 대기 후 재시도
                continue
            except MemoryError as e:
                # 메모리 부족 에러
                system_status = self.get_system_status()
                error_msg = f"메모리 부족 오류: {str(e)} [{system_status}]"
                self.logger.error(error_msg)
                self.root.after(0, self.update_status, "메모리 부족 - 메모리 정리 후 재시도", capture_count)
                gc.collect()  # 메모리 정리 시도
                time.sleep(5)  # 메모리 회복 대기
                continue
            except PermissionError as e:
                # 권한 관련 에러
                error_msg = f"권한 오류: {str(e)}"
                self.logger.error(error_msg)
                self.root.after(0, self.update_status, error_msg, capture_count)
                break
            except Exception as e:
                # 기타 예기치 않은 에러
                error_msg = f"예기치 않은 오류: {str(e)}"
                self.logger.exception(error_msg)
                self.root.after(0, self.update_status, error_msg, capture_count)
                break
    
    def update_status(self, status_text, count):
        """상태 및 카운트 업데이트"""
        self.status_label.config(text=status_text)
        self.count_label.config(text=f"캡처된 이미지: {count}개")
    
    def start_capture_automatically(self):
        """프로그램 시작 시 자동으로 캡처 시작"""
        try:
            # 현재 입력값 유효성 검사
            try:
                interval_value = float(self.interval_entry.get())
                if not (0.1 <= interval_value <= 3600):
                    self.logger.warning("자동 캡처 시작 실패: 유효하지 않은 간격 값")
                    self.status_label.config(text="자동 캡처 시작 실패 - 간격 값 확인 필요")
                    return
            except ValueError:
                self.logger.warning("자동 캡처 시작 실패: 잘못된 숫자 형식")
                self.status_label.config(text="자동 캡처 시작 실패 - 숫자 값 확인 필요")
                return

            # 자동 삭제 설정 검증 (활성화된 경우)
            if self.rolling_cleanup_enabled.get():
                if not self.validate_cleanup_interval():
                    self.logger.warning("자동 캡처 시작 실패: 자동 삭제 설정 검증 실패")
                    self.status_label.config(text="자동 캡처 시작 실패 - 삭제 설정 확인 필요")
                    return

            # 캡처 시작
            self.is_capturing = True
            self.stop_event.clear()  # 중지 신호 초기화
            self.start_button.config(text="캡처 정지")
            self.status_label.config(text="자동 캡처 시작됨...")

            # 트레이 메뉴 업데이트
            self.update_tray_menu()

            # 간격 입력 필드 비활성화 (캡처 중에는 변경 불가)
            self.interval_entry.config(state='readonly')
            self.apply_button.config(state='disabled')

            # 경로 변경 버튼 비활성화 (캡처 중에는 변경 불가)
            self.path_button.config(state='disabled')

            # 자동 삭제 설정 비활성화 (캡처 중에는 변경 불가)
            self.cleanup_checkbox.config(state='disabled')
            self.cleanup_age_entry.config(state='readonly')
            self.cleanup_unit_combo.config(state='readonly')

            # 별도 스레드에서 캡처 시작
            self.capture_thread = threading.Thread(target=self.capture_screen, daemon=True)
            self.capture_thread.start()

            # 자동 삭제가 활성화되어 있으면 스레드와 타이머 시작
            if self.rolling_cleanup_enabled.get():
                # RollingCleanup 스레드가 없으면 생성
                if self.rolling_cleanup is None:
                    cleanup_age_value = self.rolling_cleanup_age_value.get()
                    unit = self.rolling_cleanup_age_unit.get()
                    if unit == "분":
                        cleanup_age_seconds = cleanup_age_value * 60
                    else:  # "시간"
                        cleanup_age_seconds = cleanup_age_value * 3600

                    self.rolling_cleanup = RollingCleanup(
                        self.save_folder,
                        self.logger,
                        self.stop_event,
                        cleanup_age_seconds
                    )

                # RollingCleanup 스레드 시작
                self.rolling_cleanup.start()

                # UI 타이머도 시작
                self.start_cleanup_timer()

            self.logger.info("자동 캡처 시작됨")

        except Exception as e:
            self.logger.error(f"자동 캡처 시작 중 오류: {str(e)}")
            self.status_label.config(text=f"자동 캡처 시작 실패: {str(e)}")

    def toggle_capture(self):
        """캡처 시작/정지 토글"""
        if not self.is_capturing:
            # 현재 입력값 유효성 검사
            try:
                interval_value = float(self.interval_entry.get())
                if not (0.1 <= interval_value <= 3600):
                    self.interval_info.config(text="❌ 올바른 간격을 입력하세요 (0.1-3600초)", foreground="red")
                    return
            except ValueError:
                self.interval_info.config(text="❌ 올바른 숫자를 입력하세요", foreground="red")
                return

            # 자동 삭제 설정 검증 (활성화된 경우)
            if self.rolling_cleanup_enabled.get():
                if not self.validate_cleanup_interval():
                    return

            # 캡처 시작
            self.is_capturing = True
            self.stop_event.clear()  # 중지 신호 초기화
            self.start_button.config(text="캡처 정지")
            self.status_label.config(text="캡처 준비 중...")

            # 트레이 메뉴 업데이트
            self.update_tray_menu()

            # 간격 입력 필드 비활성화 (캡처 중에는 변경 불가)
            self.interval_entry.config(state='readonly')
            self.apply_button.config(state='disabled')

            # 경로 변경 버튼 비활성화 (캡처 중에는 변경 불가)
            self.path_button.config(state='disabled')

            # 자동 삭제 설정 비활성화 (캡처 중에는 변경 불가)
            self.cleanup_checkbox.config(state='disabled')
            self.cleanup_age_entry.config(state='readonly')
            self.cleanup_unit_combo.config(state='readonly')

            # 별도 스레드에서 캡처 시작
            self.capture_thread = threading.Thread(target=self.capture_screen, daemon=True)
            self.capture_thread.start()

            # 자동 삭제가 활성화되어 있으면 스레드와 타이머 시작
            if self.rolling_cleanup_enabled.get():
                # RollingCleanup 스레드가 없으면 생성
                if self.rolling_cleanup is None:
                    cleanup_age_value = self.rolling_cleanup_age_value.get()
                    unit = self.rolling_cleanup_age_unit.get()
                    if unit == "분":
                        cleanup_age_seconds = cleanup_age_value * 60
                    else:  # "시간"
                        cleanup_age_seconds = cleanup_age_value * 3600

                    self.rolling_cleanup = RollingCleanup(
                        self.save_folder,
                        self.logger,
                        self.stop_event,
                        cleanup_age_seconds
                    )

                # RollingCleanup 스레드 시작
                self.rolling_cleanup.start()

                # UI 타이머도 시작
                self.start_cleanup_timer()

        else:
            # 캡처 정지
            self.is_capturing = False
            self.start_button.config(text="캡처 시작")
            self.status_label.config(text="캡처 정지됨")

            # 트레이 메뉴 업데이트
            self.update_tray_menu()

            # 자동 삭제 스레드와 타이머 중지
            if self.rolling_cleanup_enabled.get():
                # RollingCleanup 스레드 중지
                if self.rolling_cleanup is not None:
                    self.rolling_cleanup.stop()
                    self.rolling_cleanup = None
                # UI 타이머 중지
                self.stop_cleanup_timer()

            # 간격 입력 필드 다시 활성화
            self.interval_entry.config(state='normal')
            self.apply_button.config(state='normal')

            # 경로 변경 버튼 다시 활성화
            self.path_button.config(state='normal')

            # 자동 삭제 설정 다시 활성화
            self.cleanup_checkbox.config(state='normal')
            self.cleanup_age_entry.config(state='normal')
            self.cleanup_unit_combo.config(state='normal')

            # 자동 삭제 정보 초기화 (자동 삭제 스레드는 독립적으로 동작)
            # self.next_cleanup_label.config(text="") -> 타이머가 관리하므로 주석 처리
    
    def quit_program(self):
        """프로그램 종료"""
        self.logger.info("프로그램 종료 시작")
        
        if self.is_capturing:
            # 캡처 중이면 먼저 정지
            self.is_capturing = False
            self.stop_event.set()  # 스레드 중지 신호 설정
            self.interval_entry.config(state='normal')  # 입력 필드 다시 활성화
            self.apply_button.config(state='normal')
            self.path_button.config(state='normal')  # 경로 버튼 다시 활성화
            self.cleanup_checkbox.config(state='normal')  # 자동 삭제 체크박스 다시 활성화

            # 트레이 메뉴 업데이트 (프로그램 종료 전 마지막 업데이트)
            self.update_tray_menu()
            if self.capture_thread and self.capture_thread.is_alive():
                self.capture_thread.join(timeout=5)  # 더 긴 타임아웃으로 정상 종료 대기
                if self.capture_thread.is_alive():
                    self.logger.warning("캡처 스레드가 정상적으로 종료되지 않음")
                else:
                    self.logger.info("캡처 스레드 정리 완료")
        
        # 자동 삭제 스레드 중지
        if self.rolling_cleanup is not None:
            self.rolling_cleanup.stop()
            self.rolling_cleanup = None
            self.logger.info("자동 삭제 스레드 정리 완료")

        # 리소스 모니터링 중지
        if self.resource_monitor_enabled.get():
            self.stop_resource_monitoring()
            if self.resource_monitor_thread and self.resource_monitor_thread.is_alive():
                # 리소스 모니터링 스레드는 daemon이므로 자동으로 종료됨
                self.logger.info("리소스 모니터링 스레드 정리 완료")
        
        # 종료 확인
        if messagebox.askokcancel("종료", "프로그램을 종료하시겠습니까?"):
            self.logger.info("프로그램 종료 확인됨")

            # 시스템 트레이 정리
            if self.tray_icon:
                self.tray_icon.stop()

            self.root.destroy()

    def show_startup_message(self):
        """시작 메시지 표시"""
        try:
            # 시작 메시지 창 생성
            startup_window = tk.Toplevel(self.root)
            startup_window.title("")
            startup_window.geometry("300x100")
            startup_window.resizable(False, False)
            startup_window.attributes("-topmost", True)  # 항상 위에 표시

            # 창을 화면 중앙에 위치시키기
            startup_window.update_idletasks()
            width = startup_window.winfo_width()
            height = startup_window.winfo_height()
            x = (startup_window.winfo_screenwidth() // 2) - (width // 2)
            y = (startup_window.winfo_screenheight() // 2) - (height // 2)
            startup_window.geometry(f"+{x}+{y}")

            # 메시지 라벨
            message_label = tk.Label(
                startup_window,
                text="모니터링 시작",
                font=("Arial", 16, "bold"),
                fg="blue"
            )
            message_label.pack(expand=True)

            # 1.5초 후 자동으로 창 닫기
            startup_window.after(1500, startup_window.destroy)

        except Exception as e:
            self.logger.error(f"시작 메시지 표시 실패: {str(e)}")
    
    def run(self):
        """프로그램 실행"""
        # 시작 메시지 표시
        self.show_startup_message()

        # GUI 창 숨김 (시스템 트레이로 실행)
        self.root.after(500, self.hide_window)  # 0.5초 후 숨김

        self.root.mainloop()


if __name__ == "__main__":
    try:
        app = ScreenCapture()
        app.run()
    except Exception as e:
        print(f"프로그램 실행 중 오류가 발생했습니다: {e}")
        input("Enter 키를 눌러 종료하세요...")
