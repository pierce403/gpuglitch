use glitch_contract::ProvisionalBaseline;
use serde_json::json;
use std::env;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "ga100-capture-review FILE [--baseline FILE]";
const MAX_BYTES: u64 = 1024 * 1024;

fn read_capture(path: &Path) -> Result<ProvisionalBaseline, String> {
    let read = || -> io::Result<Vec<u8>> {
        if !path.metadata()?.is_file() {
            return Err(io::Error::other("expected a regular JSON file"));
        }
        let file = File::open(path)?;
        let mut bytes = Vec::new();
        file.take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_BYTES {
            return Err(io::Error::other("file exceeds 1 MiB"));
        }
        Ok(bytes)
    };
    let bytes = read().map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_slice(&bytes).map_err(|e| {
        format!(
            "{}: expected Kate's v4 ProvisionalBaseline JSON (line {}, column {})",
            path.display(),
            e.line(),
            e.column()
        )
    })
}

fn run() -> Result<(), String> {
    let mut args = env::args_os().skip(1);
    let first = args.next().ok_or(USAGE)?;
    if first == "--help" || first == "-h" {
        println!(
            "Usage: {USAGE}\nReview saved capture quality and optionally compare a stable baseline."
        );
        return Ok(());
    }
    let candidate = PathBuf::from(first);
    let baseline_path = match args.next() {
        None => None,
        Some(flag) if flag == "--baseline" => Some(PathBuf::from(args.next().ok_or(USAGE)?)),
        _ => return Err(USAGE.into()),
    };
    if args.next().is_some() {
        return Err(USAGE.into());
    }

    let capture = read_capture(&candidate)?;
    let baseline = baseline_path.as_deref().map(read_capture).transpose()?;
    let assessment = gpu0_analysis::analyze(&capture, baseline.as_ref())
        .map_err(|error| format!("capture validation failed: {error}"))?;
    let report = json!({
        "schema_version": 1,
        "capture_id": assessment.capture_id,
        "pci_bdf": capture.gpu0_bdf,
        "stable": assessment.stable_fingerprint.is_some(),
        "relationship": assessment.relationship,
        "capture_reported_failure": capture.failure.is_some(),
        "pass_counts": {
            "rows": [capture.rows_first.len(), capture.rows_second.len()],
            "options": [capture.options_before.len(), capture.options_after.len()],
            "statuses": [capture.statuses_before.len(), capture.statuses_after.len()],
            "core": [capture.core_before.len(), capture.core_after.len()]
        }
    });
    let mut out = io::stdout().lock();
    writeln!(out, "{report:#}").map_err(|error| format!("could not write report: {error}"))
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("ga100-capture-review: {message}");
            ExitCode::from(2)
        }
    }
}
