fn main() {
    println!("cargo:rerun-if-changed=assets/league_highlights.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        // WebView2's static loader (webview2-com-sys) uses ETW from advapi32.
        // windows 0.58 linked it implicitly; windows 0.60 uses raw-dylib.
        println!("cargo:rustc-link-lib=advapi32");
        let mut res = winresource::WindowsResource::new();
        res.set_icon("assets/league_highlights.ico");
        res.set("FileDescription", "League Highlights");
        res.set("ProductName", "League Highlights");
        // A missing resource compiler only costs the Explorer icon; the tray
        // icon is decoded from the embedded .ico at runtime.
        if let Err(err) = res.compile() {
            println!("cargo:warning=icon resource not embedded: {err}");
        }
    }
}
