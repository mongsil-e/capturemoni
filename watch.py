import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import ImageGrab, ImageDraw, ImageFont, Image
import threading
import time
import os
import psutil
import logging
from datetime import datetime


class ScreenCapture:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("화면 캡처")
        self.root.geometry("450x900")  # 높이 증가 (이미지 설정 영역 추가)
        self.root.resizable(False, False)
        
        # 로깅 설정
        self.setup_logging()
        
        # 캡처 상태 관리
        self.is_capturing = False
        self.capture_thread = None
        
        # 캡처 간격 설정 (초)
        self.capture_interval = tk.DoubleVar(value=1.0)
        
        # 자동 삭제 설정
        self.auto_cleanup_enabled = tk.BooleanVar(value=False)
        self.cleanup_hours = tk.IntVar(value=1)  # 시간 단위
        self.cleanup_minutes = tk.IntVar(value=0)  # 분 단위
        self.cleanup_thread = None
        self.cleanup_timer_start = None
        self.next_cleanup_time = None
        
        # 프로그램 리소스 모니터링
        self.resource_monitor_enabled = tk.BooleanVar(value=True)
        self.resource_monitor_thread = None
        self.current_process = None

        # 이미지 설정
        self.image_format = tk.StringVar(value="JPEG")  # "JPEG" 또는 "WEBP"
        self.image_quality = tk.IntVar(value=80)  # 1-100
        self.image_quality_value = tk.DoubleVar(value=80.0)  # Scale용 실수 값
        self.image_resolution = tk.StringVar(value="원본")  # 해상도 설정
        self.image_grayscale = tk.BooleanVar(value=False)  # 흑백 변환 설정

        # 저장 폴더 설정
        self.save_folder = "screenshots"
        if not os.path.exists(self.save_folder):
            os.makedirs(self.save_folder)
        
        self.setup_ui()
        
        # 리소스 모니터링 시작
        self.start_resource_monitoring()
    
    def setup_logging(self):
        """로깅 시스템 설정"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('screen_capture.log', encoding='utf-8'),
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
        title_label = ttk.Label(main_frame, text="화면 캡처", 
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
        self.cleanup_checkbox = ttk.Checkbutton(cleanup_frame, text="자동 삭제 활성화", 
                                               variable=self.auto_cleanup_enabled,
                                               command=self.toggle_cleanup_settings)
        self.cleanup_checkbox.pack(anchor=tk.W, pady=(0, 5))
        
        # 삭제 간격 설정 프레임
        cleanup_interval_frame = ttk.Frame(cleanup_frame)
        cleanup_interval_frame.pack(fill=tk.X, pady=5)
        
        # 삭제 간격 라벨
        ttk.Label(cleanup_interval_frame, text="삭제 간격:").pack(side=tk.LEFT, padx=(0, 5))
        
        # 시간 설정
        self.cleanup_hours_spinbox = ttk.Spinbox(cleanup_interval_frame, from_=0, to=24, 
                                                width=4, textvariable=self.cleanup_hours,
                                                command=self.validate_cleanup_interval)
        self.cleanup_hours_spinbox.pack(side=tk.LEFT, padx=2)
        self.cleanup_hours_spinbox.config(state='disabled')
        self.cleanup_hours_spinbox.bind('<KeyRelease>', self.validate_cleanup_interval)
        
        ttk.Label(cleanup_interval_frame, text="시간").pack(side=tk.LEFT, padx=(2, 5))
        
        # 분 설정
        self.cleanup_minutes_spinbox = ttk.Spinbox(cleanup_interval_frame, from_=0, to=59, 
                                                  width=4, textvariable=self.cleanup_minutes,
                                                  command=self.validate_cleanup_interval)
        self.cleanup_minutes_spinbox.pack(side=tk.LEFT, padx=2)
        self.cleanup_minutes_spinbox.config(state='disabled')
        self.cleanup_minutes_spinbox.bind('<KeyRelease>', self.validate_cleanup_interval)
        
        ttk.Label(cleanup_interval_frame, text="분마다").pack(side=tk.LEFT, padx=(2, 0))
        
        # 삭제 간격 안내 라벨
        self.cleanup_info = ttk.Label(cleanup_frame, text="최소 1분 ~ 최대 24시간 설정 가능", 
                                     font=("Arial", 8), foreground="gray")
        self.cleanup_info.pack(pady=(5, 0))
        
        # 다음 삭제 시간 표시 라벨
        self.next_cleanup_label = ttk.Label(cleanup_frame, text="", 
                                           font=("Arial", 9), foreground="blue")
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
        self.root.protocol("WM_DELETE_WINDOW", self.quit_program)

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
    
    def toggle_cleanup_settings(self):
        """자동 삭제 설정 토글"""
        if self.auto_cleanup_enabled.get():
            self.cleanup_hours_spinbox.config(state='normal')
            self.cleanup_minutes_spinbox.config(state='normal')
            self.validate_cleanup_interval()
        else:
            self.cleanup_hours_spinbox.config(state='disabled')
            self.cleanup_minutes_spinbox.config(state='disabled')
            self.cleanup_info.config(text="최소 1분 ~ 최대 24시간 설정 가능 (0시간 0분은 불가)", foreground="gray")
            self.next_cleanup_label.config(text="")
    
    def validate_cleanup_interval(self, event=None):
        """자동 삭제 간격 검증"""
        try:
            hours = self.cleanup_hours.get()
            minutes = self.cleanup_minutes.get()
            
            # 총 시간을 분으로 계산
            total_minutes = hours * 60 + minutes
            
            if total_minutes == 0:
                self.cleanup_info.config(text="❌ 최소 1분 이상 설정해야 합니다", foreground="red")
                return False
            elif total_minutes > 24 * 60:  # 24시간 초과
                self.cleanup_info.config(text="❌ 최대 24시간까지 설정 가능합니다", foreground="red")
                return False
            else:
                if total_minutes < 60:
                    time_str = f"{total_minutes}분"
                else:
                    hour_part = total_minutes // 60
                    min_part = total_minutes % 60
                    if min_part == 0:
                        time_str = f"{hour_part}시간"
                    else:
                        time_str = f"{hour_part}시간 {min_part}분"
                
                self.cleanup_info.config(text=f"✅ {time_str}마다 자동 삭제됩니다", foreground="green")
                return True
                
        except (ValueError, tk.TclError):
            self.cleanup_info.config(text="❌ 올바른 숫자를 입력하세요", foreground="red")
            return False
    
    def get_cleanup_interval_seconds(self):
        """자동 삭제 간격을 초 단위로 반환"""
        hours = self.cleanup_hours.get()
        minutes = self.cleanup_minutes.get()
        return (hours * 60 + minutes) * 60
    
    def start_cleanup_timer(self):
        """자동 삭제 타이머 시작"""
        if not self.auto_cleanup_enabled.get():
            return
            
        # 간격 유효성 검사
        if not self.validate_cleanup_interval():
            return
            
        self.cleanup_timer_start = time.time()
        cleanup_seconds = self.get_cleanup_interval_seconds()
        self.next_cleanup_time = self.cleanup_timer_start + cleanup_seconds
        
        # 다음 삭제 시간 표시 업데이트 시작
        self.update_cleanup_countdown()
        
        # 별도 스레드에서 타이머 실행
        self.cleanup_thread = threading.Thread(target=self.cleanup_timer_worker, daemon=True)
        self.cleanup_thread.start()
    
    def cleanup_timer_worker(self):
        """자동 삭제 타이머 작업자"""
        cleanup_seconds = self.get_cleanup_interval_seconds()
        
        while self.is_capturing and self.auto_cleanup_enabled.get():
            time.sleep(1)  # 1초마다 체크
            
            if not self.is_capturing:  # 캡처가 중지되면 종료
                break
                
            current_time = time.time()
            if current_time >= self.next_cleanup_time:
                # 자동 삭제 실행
                self.perform_cleanup()
                
                # 다음 삭제 시간 설정
                self.next_cleanup_time = current_time + cleanup_seconds
    
    def update_cleanup_countdown(self):
        """삭제까지 남은 시간 표시 업데이트"""
        if not self.is_capturing or not self.auto_cleanup_enabled.get():
            self.next_cleanup_label.config(text="")
            return
            
        if self.next_cleanup_time:
            current_time = time.time()
            remaining = self.next_cleanup_time - current_time
            
            if remaining > 0:
                hours = int(remaining // 3600)
                minutes = int((remaining % 3600) // 60)
                seconds = int(remaining % 60)
                
                if hours > 0:
                    time_str = f"{hours}시간 {minutes}분 {seconds}초"
                elif minutes > 0:
                    time_str = f"{minutes}분 {seconds}초"
                else:
                    time_str = f"{seconds}초"
                    
                self.next_cleanup_label.config(text=f"다음 삭제까지: {time_str}")
            else:
                self.next_cleanup_label.config(text="삭제 진행 중...")
        
        # 1초 후 다시 업데이트
        if self.is_capturing and self.auto_cleanup_enabled.get():
            self.root.after(1000, self.update_cleanup_countdown)
    
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
        while self.resource_monitor_enabled.get():
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
                
                # 스크린샷 폴더 크기 계산
                folder_size_mb = self.get_folder_size_mb(self.save_folder)
                
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
        """폴더 크기를 MB 단위로 계산"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(folder_path):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(file_path)
                    except (OSError, FileNotFoundError):
                        # 파일이 삭제되었거나 접근할 수 없는 경우 무시
                        pass
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
    
    def perform_cleanup(self):
        """이미지 삭제 및 캡처 재시작 (개선된 버전)"""
        try:
            # GUI 업데이트 (삭제 시작 알림)
            self.root.after(0, lambda: self.status_label.config(text="이미지 삭제 중..."))
            self.logger.info("자동 삭제 시작")
            
            # screenshots 폴더의 모든 이미지 파일 삭제
            deleted_count = 0
            failed_count = 0
            
            for filename in os.listdir(self.save_folder):
                if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.webp')):
                    file_path = os.path.join(self.save_folder, filename)
                    
                    # 안전한 삭제 함수 사용
                    if self.safe_delete_file(file_path):
                        deleted_count += 1
                    else:
                        failed_count += 1
            
            # 결과 로깅
            if failed_count > 0:
                self.logger.warning(f"자동 삭제 완료: 성공 {deleted_count}개, 실패 {failed_count}개")
                status_msg = f"삭제 완료 ({deleted_count}개 성공, {failed_count}개 실패) - 캡처 재시작"
            else:
                self.logger.info(f"자동 삭제 완료: {deleted_count}개 파일 삭제")
                status_msg = f"삭제 완료 ({deleted_count}개 파일) - 캡처 재시작"
            
            # GUI 업데이트 (삭제 완료 및 캡처 재시작)
            self.root.after(0, lambda: self.status_label.config(text=status_msg))
            self.root.after(0, lambda: self.count_label.config(text="캡처된 이미지: 0개"))
            
        except Exception as e:
            error_msg = f"삭제 중 오류: {str(e)}"
            self.logger.error(error_msg)
            self.root.after(0, lambda: self.status_label.config(text=error_msg))
        
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
    
    def capture_screen(self):
        """화면 캡처 함수"""
        capture_count = 0
        
        while self.is_capturing:
            try:
                # 전체 화면 캡처
                screenshot = ImageGrab.grab()
                
                # 현재 시간 오버레이 추가
                screenshot_with_time = self.add_timestamp_overlay(screenshot)

                # 해상도 조정 적용 (thumbnail 사용)
                if self.image_resolution.get() != "원본":
                    width, height = map(int, self.image_resolution.get().split('x'))
                    screenshot_with_time.thumbnail((width, height), Image.LANCZOS)

                # 흑백 변환 적용
                if self.image_grayscale.get():
                    screenshot_with_time = screenshot_with_time.convert('L')

                # 파일명 생성 (타임스탬프 포함)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 밀리초까지
                format_ext = "webp" if self.image_format.get() == "WEBP" else "jpg"
                filename = f"screenshot_{timestamp}.{format_ext}"
                filepath = os.path.join(self.save_folder, filename)

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
                
            except Exception as e:
                # 에러 발생 시 GUI 업데이트
                self.root.after(0, self.update_status, f"에러 발생: {str(e)}", capture_count)
                break
    
    def update_status(self, status_text, count):
        """상태 및 카운트 업데이트"""
        self.status_label.config(text=status_text)
        self.count_label.config(text=f"캡처된 이미지: {count}개")
    
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
            if self.auto_cleanup_enabled.get():
                if not self.validate_cleanup_interval():
                    return
            
            # 캡처 시작
            self.is_capturing = True
            self.start_button.config(text="캡처 정지")
            self.status_label.config(text="캡처 준비 중...")
            
            # 간격 입력 필드 비활성화 (캡처 중에는 변경 불가)
            self.interval_entry.config(state='readonly')
            self.apply_button.config(state='disabled')
            
            # 경로 변경 버튼 비활성화 (캡처 중에는 변경 불가)
            self.path_button.config(state='disabled')
            
            # 자동 삭제 설정 비활성화 (캡처 중에는 변경 불가)
            self.cleanup_checkbox.config(state='disabled')
            self.cleanup_hours_spinbox.config(state='disabled')
            self.cleanup_minutes_spinbox.config(state='disabled')
            
            # 별도 스레드에서 캡처 시작
            self.capture_thread = threading.Thread(target=self.capture_screen, daemon=True)
            self.capture_thread.start()
            
            # 자동 삭제 타이머 시작
            self.start_cleanup_timer()
            
        else:
            # 캡처 정지
            self.is_capturing = False
            self.start_button.config(text="캡처 시작")
            self.status_label.config(text="캡처 정지됨")
            
            # 간격 입력 필드 다시 활성화
            self.interval_entry.config(state='normal')
            self.apply_button.config(state='normal')
            
            # 경로 변경 버튼 다시 활성화
            self.path_button.config(state='normal')
            
            # 자동 삭제 설정 다시 활성화
            self.cleanup_checkbox.config(state='normal')
            if self.auto_cleanup_enabled.get():
                self.cleanup_hours_spinbox.config(state='normal')
                self.cleanup_minutes_spinbox.config(state='normal')
            
            # 자동 삭제 타이머 정보 초기화
            self.next_cleanup_time = None
            self.next_cleanup_label.config(text="")
    
    def quit_program(self):
        """프로그램 종료"""
        self.logger.info("프로그램 종료 시작")
        
        if self.is_capturing:
            # 캡처 중이면 먼저 정지
            self.is_capturing = False
            self.interval_entry.config(state='normal')  # 입력 필드 다시 활성화
            self.apply_button.config(state='normal')
            self.path_button.config(state='normal')  # 경로 버튼 다시 활성화
            self.cleanup_checkbox.config(state='normal')  # 자동 삭제 체크박스 다시 활성화
            if self.auto_cleanup_enabled.get():
                self.cleanup_hours_spinbox.config(state='normal')
                self.cleanup_minutes_spinbox.config(state='normal')
            if self.capture_thread and self.capture_thread.is_alive():
                self.capture_thread.join(timeout=2)
                self.logger.info("캡처 스레드 정리 완료")
        
        # 리소스 모니터링 중지
        if self.resource_monitor_enabled.get():
            self.stop_resource_monitoring()
            if self.resource_monitor_thread and self.resource_monitor_thread.is_alive():
                # 리소스 모니터링 스레드는 daemon이므로 자동으로 종료됨
                self.logger.info("리소스 모니터링 스레드 정리 완료")
        
        # 종료 확인
        if messagebox.askokcancel("종료", "프로그램을 종료하시겠습니까?"):
            self.logger.info("프로그램 종료 확인됨")
            self.root.destroy()
    
    def run(self):
        """프로그램 실행"""
        self.root.mainloop()


if __name__ == "__main__":
    try:
        app = ScreenCapture()
        app.run()
    except Exception as e:
        print(f"프로그램 실행 중 오류가 발생했습니다: {e}")
        input("Enter 키를 눌러 종료하세요...")
