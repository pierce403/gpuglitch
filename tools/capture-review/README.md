# Reviewing a saved GPU capture

`ga100-capture-review` reads a local JSON serialization of Kate's version 4 `ProvisionalBaseline`. It checks the map digests, source fields and agreement between two passes using Kate's `gpu0_analysis::analyze`. The resulting JSON contains counts, capture ID, PCI address and the relationship to an optional baseline. It opens only the JSON files you select and runs without administrator privileges.

From the gpuglitch repository root, install the helper with Rust 1.97 or newer and run the bundled synthetic example.

```sh
cargo install --locked --path tools/capture-review
ga100-capture-review tools/capture-review/examples/partial.json
```

The example models a capture interrupted before its first measurement. Expect `stable: false`, `capture_reported_failure: true` and `relationship: "incomplete_preserve"`. An input can also be incomplete when a pass is short or the two passes disagree, even if no capture failure was recorded. Capture files may be up to 1 MiB each.

For a comparison, supply a complete stable enrollment from the same source.

```sh
ga100-capture-review capture.json --baseline enrolled.json
```

| Relationship | Interpretation |
| --- | --- |
| `incomplete_preserve` | The candidate was partial or its passes disagreed. |
| `uncompared_preserve` | The candidate was stable and no reference was supplied. |
| `baseline_match` | The stable candidate and reference have the same fingerprint. |
| `changed_preserve` | The stable candidate and reference have different fingerprints. |

Exit 0 means the tool produced a report. Exit 2 covers unreadable or unsupported input, incompatible register maps and invalid references. The source check compares the recorded specimen claim, PCI ID and PCI address. The stability result describes the saved observations. It cannot establish the physical card's identity or capacity. Pico CLI shadow-register dumps use a different JSON format.

The analysis and data types come from Kate's MIT-licensed repository at commit `b71e3990cc652a07cdbb5c6d1192e3e8116692d2`. Cargo fetches the pinned libraries at installation time and builds them without the optional Linux BAR0 transport. [Analysis code](https://github.com/kate-land/ga100vfi/tree/b71e3990cc652a07cdbb5c6d1192e3e8116692d2/gpu0-registers/crates/gpu0-analysis) · [Upstream license](https://github.com/kate-land/ga100vfi/blob/b71e3990cc652a07cdbb5c6d1192e3e8116692d2/LICENSE)
