# 릴리스 빌드 절차

이 문서는 capturemoni(intervalcapture) 클라이언트 배포 빌드 절차이다. 클라이언트는 64비트와 32비트 두 가지로 빌드하며, 둘 다 Windows 7 / 10 / 11에서 실행되어야 한다.

## 공통 준비

Rust는 Windows 7 클라이언트 호환성을 위해 1.77.2를 사용한다. `rust/rust-toolchain.toml`에도 같은 버전이 지정되어 있다.

```powershell
rustup toolchain install 1.77.2
rustup +1.77.2 target add x86_64-pc-windows-msvc
rustup +1.77.2 target add i686-pc-windows-msvc
```

`rust/.cargo/config.toml`에는 두 타깃 모두 MSVC CRT 정적 링크 옵션을 둔다.

```toml
[target.x86_64-pc-windows-msvc]
rustflags = ["-C", "target-feature=+crt-static"]

[target.i686-pc-windows-msvc]
rustflags = ["-C", "target-feature=+crt-static"]
```

의존성 잠금: `Cargo.lock`은 Rust 1.77.2 호환을 위해 일부 크레이트(cc, jobserver, idna_adapter, unicode-segmentation 등)를 구버전으로 고정했다. 빌드는 반드시 `--locked`로 수행하고, 새 의존성을 추가할 때는 최신 stable cargo(MSRV 인식 리졸버)로 lockfile을 갱신한 뒤 1.77.2로 빌드가 되는지 확인한다.

## 클라이언트 빌드 (64비트 / 32비트)

```powershell
cd rust
cargo +1.77.2 build --release --locked --target x86_64-pc-windows-msvc
cargo +1.77.2 build --release --locked --target i686-pc-windows-msvc
```

산출물:

- 64비트: `rust\target\x86_64-pc-windows-msvc\release\intervalcapture.exe`
- 32비트: `rust\target\i686-pc-windows-msvc\release\intervalcapture.exe`

## OpenGL 폴백 DLL (Mesa llvmpipe) — 아키텍처별로 다름

Windows 7 VM처럼 OpenGL 2.0을 제공하지 못하는 환경을 대비해 Mesa llvmpipe의 `opengl32.dll`을 `intervalcapture.exe`와 같은 폴더에 포함한다. **이 DLL은 exe 옆에 있으면 시스템 OpenGL보다 먼저 로드되므로, Windows 7 비호환 DLL을 넣으면 정상 GPU가 있는 Win7에서도 앱이 아예 실행되지 않는다.** 반드시 아키텍처와 Windows 7 호환성을 맞춘 DLL을 사용한다.

중요 — Mesa 버전별 Windows 7 호환성:

- **64비트: Mesa llvmpipe 24.1.2** (mmozeiko/build-mesa). Win7 안전.
- **32비트: Mesa llvmpipe 24.3.4 x86** (mmozeiko의 x86 빌드 중 가장 오래된 버전). Win7 안전.
- **26.x는 32/64비트 모두 사용 금지.** 26.x의 `opengl32.dll`은 Windows 8부터 추가된 `GetSystemTimePreciseAsFileTime`을 KERNEL32에서 정적 import한다. Windows 7에는 이 함수가 없어 로드 시점에 `프로시저 진입점 GetSystemTimePreciseAsFileTime을(를) ... KERNEL32.dll에서 찾을 수 없습니다` 오류로 실행이 실패한다. (일부 26.x는 `api-ms-*` apiset도 import한다.) 32비트 x86 빌드는 24.3.4까지만 Win7 안전이 확인되었다.

64비트 DLL 내려받기(24.1.2, `.zip`):

```powershell
New-Item -ItemType Directory -Force -Path target\mesa-candidates | Out-Null
Invoke-WebRequest `
  -Uri https://github.com/mmozeiko/build-mesa/releases/download/24.1.2/mesa-llvmpipe-24.1.2.zip `
  -OutFile target\mesa-candidates\mesa-llvmpipe-24.1.2.zip
New-Item -ItemType Directory -Force -Path target\mesa-candidates\24.1.2 | Out-Null
Expand-Archive -LiteralPath target\mesa-candidates\mesa-llvmpipe-24.1.2.zip `
  -DestinationPath target\mesa-candidates\24.1.2 -Force
```

32비트 DLL 내려받기(24.3.4 x86, `.zip`):

```powershell
Invoke-WebRequest `
  -Uri https://github.com/mmozeiko/build-mesa/releases/download/24.3.4/mesa-llvmpipe-x86-24.3.4.zip `
  -OutFile target\mesa-candidates\mesa-llvmpipe-x86-24.3.4.zip
New-Item -ItemType Directory -Force -Path target\mesa-candidates\x86-24.3.4 | Out-Null
Expand-Archive -LiteralPath target\mesa-candidates\mesa-llvmpipe-x86-24.3.4.zip `
  -DestinationPath target\mesa-candidates\x86-24.3.4 -Force
```

클라이언트 배포물에는 `.bat` 실행 배치 파일을 만들거나 포함하지 않는다. 사용자는 `intervalcapture.exe`를 직접 실행하며, Windows 7 VM 대응은 같은 폴더에 둔 `opengl32.dll`로 처리한다.

## 배포 폴더 구성

아키텍처별로 exe와 해당 아키텍처의 `opengl32.dll`만 넣는다. 실행 중 생성된 스크린샷·로그·settings.json이 섞이지 않도록 깨끗한 스테이징 폴더에서 만든다.

```powershell
# 64비트
New-Item -ItemType Directory -Force -Path dist\pack\client-win7-x64 | Out-Null
Copy-Item rust\target\x86_64-pc-windows-msvc\release\intervalcapture.exe dist\pack\client-win7-x64\intervalcapture.exe -Force
Copy-Item rust\target\mesa-candidates\24.1.2\opengl32.dll dist\pack\client-win7-x64\opengl32.dll -Force

# 32비트
New-Item -ItemType Directory -Force -Path dist\pack\client-win7-x86 | Out-Null
Copy-Item rust\target\i686-pc-windows-msvc\release\intervalcapture.exe dist\pack\client-win7-x86\intervalcapture.exe -Force
Copy-Item rust\target\mesa-candidates\x86-24.3.4\opengl32.dll dist\pack\client-win7-x86\opengl32.dll -Force
```

## 압축 (반디집)

반디집 콘솔(`bz.exe`)로 각 아키텍처 폴더를 압축한다. 암호 압축이 필요하면 `-p:<암호>`를 붙인다.

```powershell
$bz = "C:\Program Files\Bandizip\bz.exe"
Set-Location dist\pack
& $bz c -y -r -l:9 -p:12345678 ..\intervalcapture-win7-x64.zip client-win7-x64
& $bz c -y -r -l:9 -p:12345678 ..\intervalcapture-win7-x86.zip client-win7-x86
Set-Location ..\..
```

폴더명을 인자로 넘기면 압축 내부 경로가 `client-win7-x64\...`처럼 깔끔하게 유지된다(전체 경로 접두어가 붙지 않는다).

## 검증

각 아키텍처 exe와 동봉 DLL에 대해 확인한다.

- exe가 Windows GUI 서브시스템(PE Subsystem=2)으로 빌드되어 콘솔창을 띄우지 않는지 확인
- PE OptionalHeader의 최소 OS 버전이 6.0/6.1(Windows 7 이하)인지 확인
- exe와 DLL의 아키텍처가 일치하는지 확인 (PE machine: x64=0x8664, x86=0x14c). 64비트 exe에는 64비트 DLL, 32비트 exe에는 32비트 DLL.
- **DLL이 Windows 8+ 전용 함수를 import하지 않는지 확인한다.** `api-ms-*` apiset만이 아니라 KERNEL32의 개별 함수(특히 `GetSystemTimePreciseAsFileTime`, Windows 8+)도 반드시 확인한다. dumpbin으로 import 테이블을 직접 검사:

```powershell
$dumpbin = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\<버전>\bin\Hostx64\x64\dumpbin.exe"
& $dumpbin /IMPORTS dist\pack\client-win7-x86\opengl32.dll | Select-String "GetSystemTimePreciseAsFileTime|api-ms"
# 아무것도 출력되지 않아야 Win7 안전
```

- 배포 폴더/ZIP 안에 `.bat` 파일, 스크린샷, 로그, settings.json이 없는지 확인
- 암호 압축본은 암호 없이 추출이 거부되고(`Password is needed`), 지정 암호로는 정상 추출되는지 확인
- 실행 후 창이 뜨지 않고 트레이에만 상주하며, `screenshots/`에 캡처 파일이 생성되고 `logs/년/월/일.log`에 로그가 기록되는지 확인
- 트레이 아이콘 우클릭 메뉴(표시/시작/정지/종료)가 창이 숨겨진 상태에서도 동작하는지 확인

Windows 7 클라이언트의 실제 Windows 7 실행 확인은 사용자가 수행한다. 빌드 작업 중에는 사용자가 명시적으로 요청하지 않는 한 VM을 시작하거나 조작하지 않는다.

## 참고 오류 메시지

- `프로시저 진입점 GetSystemTimePreciseAsFileTime을(를) ... KERNEL32.dll에서 찾을 수 없습니다`: Windows 8+ 전용 함수를 import하는 Mesa DLL(26.x 등)이 들어간 경우. 24.1.2(x64)/24.3.4(x86)로 교체한다.
- `api-ms-win-core-synch-l1-2-0.dll` 누락: Windows 7 비호환 Mesa DLL이 들어갔다는 뜻.
- `egui_glow requires opengl 2.0+`: VM 그래픽 드라이버가 OpenGL 2.0 이상을 제공하지 못한다는 뜻. 같은 폴더에 올바른 `opengl32.dll`이 있는지 확인한다.
- exe만 복사하지 말고 배포 폴더 전체(exe + `opengl32.dll`)를 복사한다. `opengl32.dll`은 `intervalcapture.exe`와 같은 폴더에 있어야 한다.
