import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import ImageGrab, ImageDraw, ImageFont
import threading
import time
import os
from datetime import datetime


class ScreenCapture:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("화면 캡처 프로그램")
        self.root.geometry("450x450")
        self.root.resizable(False, False)
        
        # 캡처 상태 관리
        self.is_capturing = False
        self.capture_thread = None
        
        # 캡처 간격 설정 (초)
        self.capture_interval = tk.DoubleVar(value=1.0)
        
        # 저장 폴더 설정
        self.save_folder = "screenshots"
        if not os.path.exists(self.save_folder):
            os.makedirs(self.save_folder)
        
        self.setup_ui()
        
    def setup_ui(self):
        """GUI 인터페이스 설정"""
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 제목
        title_label = ttk.Label(main_frame, text="화면 캡처 프로그램", 
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
                
                # 파일명 생성 (타임스탬프 포함)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 밀리초까지
                filename = f"screenshot_{timestamp}.jpg"
                filepath = os.path.join(self.save_folder, filename)
                
                # 이미지 최적화하여 저장 (용량 줄이기)
                screenshot_with_time.save(filepath, "JPEG", quality=75, optimize=True)
                
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
            
            # 캡처 시작
            self.is_capturing = True
            self.start_button.config(text="캡처 정지")
            self.status_label.config(text="캡처 준비 중...")
            
            # 간격 입력 필드 비활성화 (캡처 중에는 변경 불가)
            self.interval_entry.config(state='readonly')
            self.apply_button.config(state='disabled')
            
            # 경로 변경 버튼 비활성화 (캡처 중에는 변경 불가)
            self.path_button.config(state='disabled')
            
            # 별도 스레드에서 캡처 시작
            self.capture_thread = threading.Thread(target=self.capture_screen, daemon=True)
            self.capture_thread.start()
            
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
    
    def quit_program(self):
        """프로그램 종료"""
        if self.is_capturing:
            # 캡처 중이면 먼저 정지
            self.is_capturing = False
            self.interval_entry.config(state='normal')  # 입력 필드 다시 활성화
            self.apply_button.config(state='normal')
            self.path_button.config(state='normal')  # 경로 버튼 다시 활성화
            if self.capture_thread and self.capture_thread.is_alive():
                self.capture_thread.join(timeout=2)
        
        # 종료 확인
        if messagebox.askokcancel("종료", "프로그램을 종료하시겠습니까?"):
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
