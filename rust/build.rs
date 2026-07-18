fn main() {
    let mut res = winres::WindowsResource::new();
    res.set_icon("assets/icon.ico");
    if let Err(e) = res.compile() {
        println!("cargo:warning=아이콘 리소스 컴파일 실패: {}", e);
    }
    println!("cargo:rerun-if-changed=assets/icon.ico");
}
