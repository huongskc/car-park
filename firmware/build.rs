fn main() {
    embuild::espidf::sysenv::output();
    println!("cargo:rerun-if-changed=secrets.env");
    let text = std::fs::read_to_string("secrets.env").unwrap_or_else(|_| {
        panic!("Thiếu firmware/secrets.env. Sao chép secrets.env.example rồi điền giá trị cục bộ.");
    });
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if key.is_empty() {
            continue;
        }
        println!("cargo:rustc-env={key}={}", value.trim());
    }
}
