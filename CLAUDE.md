## Windows 7 호환성

- 릴리스 빌드는 Windows 7에서도 실행 가능해야 한다.
- Windows 7을 지원하지 않는 GUI 런타임이나 시스템 의존성은 기본 선택지로 사용하지 않는다.
- Windows 10 이상만 지원하는 WebView 기반 프레임워크를 사용금지, Windows 7용 대체 클라이언트 빌드 전략을 먼저 설계하고 문서화해야 한다.
- Windows 7 호환 빌드는 `i686-win7-windows-msvc`, `x86_64-win7-windows-msvc`, `i686-win7-windows-gnu`, `x86_64-win7-windows-gnu` 같은 Windows 7 전용 Rust 타깃 사용 가능성을 우선 검토한다.
- Windows 7 실제 환경이나 VM에서의 실행 검증은 사용자가 수행한다. 사용자가 명시적으로 요청하지 않는 한 에이전트는 VM을 시작하거나 조작하지 않는다.

## EXE 빌드 요청 처리

- 사용자가 exe 빌드, 릴리스 빌드, 배포 파일 생성을 요청하면 먼저 `docs/release-build.md`를 읽고 그 문서의 규칙과 절차에 따라 빌드한다.
- 클라이언트 exe는 Windows 7, Windows 10, Windows 11 호환 기준으로 빌드하고, 서버 exe는 Windows 10 기준으로 빌드한다.
- 빌드 후에는 `docs/release-build.md`에 적힌 검증 항목과 산출물 구성을 확인한다.
- 클라이언트나 서버 빌드 산출물을 만들 때 `.bat` 실행 파일은 생성하지 않는다.
