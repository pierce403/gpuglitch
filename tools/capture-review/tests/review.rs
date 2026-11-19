use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

struct Files(PathBuf);

impl Files {
    fn new() -> Self {
        let dir = std::env::temp_dir().join(format!("ga100-review-test-{}", std::process::id()));
        fs::create_dir(&dir).unwrap();
        Self(dir)
    }

    fn save(&self, name: &str, contents: &Value) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, serde_json::to_vec(contents).unwrap()).unwrap();
        path
    }
}

impl Drop for Files {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn run(files: &[&std::path::Path]) -> std::process::Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_ga100-capture-review"));
    cmd.arg(files[0]);
    if let Some(baseline) = files.get(1) {
        cmd.arg("--baseline").arg(baseline);
    }
    cmd.output().unwrap()
}

fn report(files: &[&std::path::Path]) -> Value {
    let result = run(files);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    serde_json::from_slice(&result.stdout).unwrap()
}

#[test]
fn compares_saved_evidence_and_rejects_incompatible_sources() {
    let scratch = Files::new();
    let mut capture: Value =
        serde_json::from_str(include_str!("../examples/partial.json")).unwrap();
    let partial = scratch.save("partial.json", &capture);
    assert_eq!(report(&[&partial])["relationship"], "incomplete_preserve");

    capture.as_object_mut().unwrap().remove("failure");
    for (key, count) in [
        ("rows_first", 512),
        ("rows_second", 512),
        ("options_before", 672),
        ("options_after", 672),
        ("statuses_before", 84),
        ("statuses_after", 84),
        ("core_before", 15),
        ("core_after", 15),
    ] {
        capture[key] = json!(vec![123_u32; count]);
    }
    let baseline = scratch.save("baseline.json", &capture);
    assert_eq!(report(&[&baseline])["relationship"], "uncompared_preserve");
    assert_eq!(
        report(&[&baseline, &baseline])["relationship"],
        "baseline_match"
    );

    capture["statuses_before"][0] = json!(122);
    let unstable = scratch.save("unstable.json", &capture);
    assert_eq!(report(&[&unstable, &baseline])["stable"], false);
    capture["statuses_after"][0] = json!(122);
    let changed = scratch.save("changed.json", &capture);
    assert_eq!(
        report(&[&changed, &baseline])["relationship"],
        "changed_preserve"
    );

    capture["gpu0_bdf"] = json!("0000:02:00.0");
    let other = scratch.save("other.json", &capture);
    assert_eq!(run(&[&other, &baseline]).status.code(), Some(2));
    capture["core_map_digest"] = capture["status_map_digest"].clone();
    let wrong_map = scratch.save("wrong-map.json", &capture);
    assert_eq!(run(&[&wrong_map]).status.code(), Some(2));
    let wrong_format = scratch.save("wrong-format.json", &json!({}));
    assert_eq!(run(&[&wrong_format]).status.code(), Some(2));
}
