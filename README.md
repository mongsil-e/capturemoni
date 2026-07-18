# capturemoni

일정 간격으로 전체 화면을 캡처해 저장하는 화면 모니터링 프로그램. 시스템 트레이에 상주하며, 오래된 캡처 파일을 자동 삭제한다.

두 가지 구현이 있다.

## rust/ — Rust 버전 (권장, Windows 7 호환)

- GUI: eframe(egui), 캡처: GDI(BitBlt), 트레이: tray-icon
- 기능: 캡처 간격(0.1~3600초), 저장 경로 변경, JPEG/WebP 품질·해상도·흑백 설정, 타임스탬프 오버레이, 10분 주기 자동 삭제, 날짜별 로그(`logs/년/월/일.log`), 트레이 메뉴(표시/시작/정지/종료)
- 빌드: Rust 1.77.2 + `x86_64-pc-windows-msvc` (Windows 7 호환을 위한 고정 버전)

```powershell
cd rust
cargo +1.77.2 build --release --locked --target x86_64-pc-windows-msvc
```

자세한 릴리스 빌드·배포 절차는 [docs/release-build.md](docs/release-build.md) 참고.

## python/ — Python 버전 (원본)

- tkinter + PIL + pystray 기반 원본 구현
- 실행: `python python/watch.py` (Pillow, psutil, pystray 필요)

Rust 버전은 Python 버전의 기능을 이식한 것이며, 리소스 모니터링 기능은 제거되었고 종료 확인 대화상자 없이 즉시 종료된다.
