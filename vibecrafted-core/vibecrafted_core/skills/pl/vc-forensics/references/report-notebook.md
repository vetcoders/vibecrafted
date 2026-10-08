# Offline evidence notebook

Run the helper from the skill's installed directory (not the investigated repo's
source root). It is stdlib Python, with no server, network, shell execution or
additional package installation. Both EN and PL skill packages carry identical
helper/template bytes so either package can be exported independently.

```bash
python3 /path/to/vc-forensics/scripts/render_report.py \
  --repo /path/to/investigated/repo \
  --head FULL_40_CHARACTER_SOURCE_SHA \
  --baseline FULL_40_CHARACTER_BASELINE_SHA \
  --loctree /path/to/loct-findings.json \
  --prview /path/to/prview/report.json \
  --findings /path/to/manual-findings.json \
  --out /path/to/artifacts/forensics.html
```

Obtain Loctree input with `loct findings --json` from the selected repository.
Use the existing PRView run's canonical `report.json`. Do not rerun a broad audit
merely to populate the notebook when the task does not require it. All three
input flags are optional; missing sources visibly remain `NOT_ASSESSED`.
`--repo` and `--head` explicitly bind the investigation; the helper does not infer
identity from the current directory. Existing output files and input reports
cannot be overwritten; choose a new output path for each snapshot.

## Inputs and preservation

Loctree and PRView accept JSON objects or arrays. The complete parsed input is
embedded with original absolute path, byte count and SHA-256. The inspector
compares Loctree `git_ref` and PRView `meta.range.head` against the declared SHA;
short hashes only establish prefix agreement. Mismatches stay visible. Unknown
schema sections stay in Complete input JSON; no indexed rows does not mean
zero findings or a passing report. These imports never adjudicate a defect or
certify a PRView gate as product acceptance.

The Loctree tab indexes `dead_parrots`, `cycles`, `duplicates`, `barrel_chaos` and
`quick_wins`; PRView indexes `gate`, `checks`, `checks_skipped` and `ownership`.
Every row retains its original section/index; search filters these observations.
The original sources are read once, never followed or modified.

Manual findings accept an array or this envelope:

```json
{
  "schema": "vc-forensics.findings.v1",
  "findings": [
    {
      "id": "F-01",
      "title": "Observed failure",
      "state": "hypothesis",
      "evidence": [],
      "verification": { "runtime": "NOT_ASSESSED" }
    }
  ]
}
```

`id` and `title` must be nonempty strings; ids must be unique. States follow
[the evidence contract](forensics-evidence-spec.md). Unknown receipt fields are
preserved, not silently discarded. Exported envelopes also bind repo/head;
imports with a conflicting binding are rejected.

## Browser edits

Open the generated HTML locally. Findings can be added or edited; tool imports
remain read-only. Changes live in the current tab and are marked unexported.
Export findings JSON before closing, then import that file in the next notebook
or supply it to `--findings`. Browser edits do not write to the original input,
journal, control plane or HTML automatically. Download initiation is not proof
that the file was saved. Invalid imports retain the existing notebook findings.

The artifact contains data, not executable imported markup: source text renders
through textContent, with no imported HTML, external URLs, CDN, fetch or eval.
It is an evidence projection; compare receipt paths against current reality
before an agent advances a tracker or reports completion.
