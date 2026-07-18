# 릴리스 빌드 절차

이 문서는 capturemoni(intervalcapture) 클라이언트 배포 빌드 절차이다.

## 공통 준비

Rust는 Windows 7 클라이언트 호환성을 위해 1.77.2를 사용한다. `rust/rust-toolchain.toml`에도 같은 버전이 지정되어 있다.

```powershell
rustup toolchain install 1.77.2
rustup +1.77.2 target add x86_64-pc-windows-msvc
```

`rust/.cargo/config.toml`에는 MSVC CRT 정적 링크 옵션을 둔다.

```toml
[target.x86_64-pc-windows-msvc]
rustflags = ["-C", "target-feature=+crt-static"]
```

의존성 잠금: `Cargo.lock`은 Rust 1.77.2 호환을 위해 일부 크레이트(cc, jobserver, idna_adapter, unicode-segmentation 등)를 구버전으로 고정했다. 빌드는 반드시 `--locked`로 수행하고, 새 의존성을 추가할 때는 최신 stable cargo(MSRV 인식 리졸버)로 lockfile을 갱신한 뒤 1.77.2로 빌드가 되는지 확인한다.

## 클라이언트 빌드

클라이언트는 Windows 7, Windows 10, Windows 11에서 실행되어야 하므로 Rust 1.77.2와 `x86_64-pc-windows-msvc` 타깃으로 빌드한다.

```powershell
cd rust
cargo +1.77.2 build --release --locked --target x86_64-pc-windows-msvc
```

빌드 결과를 배포 폴더에 복사한다.

```powershell
Copy-Item target\x86_64-pc-windows-msvc\release\intervalcapture.exe ..\dist\client-win7-x64\intervalcapture.exe -Force
Copy-Item target\x86_64-pc-windows-msvc\release\intervalcapture.exe ..\dist\client\intervalcapture.exe -Force
```

Windows 7 VM에서 OpenGL 2.0을 제공하지 못하는 경우를 대비해 Mesa llvmpipe 24.1.2의 `opengl32.dll`을 `intervalcapture.exe`와 같은 폴더에 포함한다.

Mesa llvmpipe 24.1.2 파일이 없으면 먼저 내려받아 압축을 푼다.

```powershell
New-Item -ItemType Directory -Force -Path target\mesa-candidates | Out-Null
Invoke-WebRequest `
  -Uri https://github.com/mmozeiko/build-mesa/releases/download/24.1.2/mesa-llvmpipe-24.1.2.zip `
  -OutFile target\mesa-candidates\mesa-llvmpipe-24.1.2.zip

New-Item -ItemType Directory -Force -Path target\mesa-candidates\24.1.2 | Out-Null
Expand-Archive `
  -LiteralPath target\mesa-candidates\mesa-llvmpipe-24.1.2.zip `
  -DestinationPath target\mesa-candidates\24.1.2 `
  -Force
```

그 다음 `opengl32.dll`을 클라이언트 배포 폴더에 복사한다.

```powershell
Copy-Item target\mesa-candidates\24.1.2\opengl32.dll ..\dist\client-win7-x64\opengl32.dll -Force
Copy-Item target\mesa-candidates\24.1.2\opengl32.dll ..\dist\client\opengl32.dll -Force
```

주의: 최신 Mesa llvmpipe 26.x의 `opengl32.dll`은 Windows 7에 없는 `api-ms-win-core-synch-l1-2-0.dll`을 import할 수 있으므로 Windows 7 배포물에 사용하지 않는다.

클라이언트 배포물에는 `.bat` 실행 배치 파일을 만들거나 포함하지 않는다. 사용자는 `intervalcapture.exe`를 직접 실행하며, Windows 7 VM 대응은 같은 폴더에 둔 `opengl32.dll`로 처리한다.

## 배포 ZIP 생성

배포 ZIP은 실행 중 생성된 스크린샷·로그가 섞이지 않도록 깨끗한 스테이징 폴더에서 만든다. `intervalcapture.exe`와 `opengl32.dll`만 포함한다.

```powershell
$StageRoot = "dist\release-staging"
$ClientStage = Join-Path $StageRoot "client-win7-x64"

Remove-Item -LiteralPath $ClientStage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $ClientStage | Out-Null
Copy-Item rust\target\x86_64-pc-windows-msvc\release\intervalcapture.exe (Join-Path $ClientStage "intervalcapture.exe") -Force
Copy-Item rust\target\mesa-candidates\24.1.2\opengl32.dll (Join-Path $ClientStage "opengl32.dll") -Force

Compress-Archive -Path "$ClientStage\*" -DestinationPath dist\intervalcapture-client-win7-x64.zip -Force
```

## 검증

- `intervalcapture.exe`가 Windows GUI 서브시스템(PE Subsystem=2)으로 빌드되어 콘솔창을 띄우지 않는지 확인
- PE OptionalHeader의 최소 OS 버전이 6.1(Windows 7) 이하인지 확인
- `opengl32.dll`이 `api-ms-win-core-synch-l1-2-0.dll` 같은 `api-ms-*` DLL을 import하지 않는지 확인
- 배포 ZIP 안에 `.bat` 파일, 스크린샷, 로그가 없는지 확인
- 실행 후 `screenshots/`에 캡처 파일이 생성되고 `logs/년/월/일.log`에 로그가 기록되는지 확인
- 트레이 아이콘 우클릭 메뉴(표시/시작/정지/종료)가 창이 숨겨진 상태에서도 동작하는지 확인

Windows 7 클라이언트 ZIP의 실제 Windows 7 실행 확인은 사용자가 수행한다. 빌드 작업 중에는 사용자가 명시적으로 요청하지 않는 한 VM을 시작하거나 조작하지 않는다.

- `intervalcapture.exe`만 복사하지 말고 배포 폴더 전체를 복사한다.
- `opengl32.dll`이 `intervalcapture.exe`와 같은 폴더에 있어야 한다.
- `egui_glow requires opengl 2.0+`는 VM 그래픽 드라이버가 OpenGL 2.0 이상을 제공하지 못한다는 뜻이다.
- `api-ms-win-core-synch-l1-2-0.dll` 누락 오류는 Windows 7 비호환 Mesa DLL이 들어갔다는 뜻이다.
