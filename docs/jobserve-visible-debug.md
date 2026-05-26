# JobServe Visible Apply Debug

Use this when the Render worker diagnostics are not enough and you need to watch the JobServe flow in a local browser.

The script uses Playwright in headed mode and calls the same JobServe search-to-apply flow used by production. It defaults to review-only mode, so it fills the application and stops before the final JobServe Apply click.

## Review-Only Run

```powershell
python backend/scripts/run_jobserve_apply_debug.py --email "me@example.com" --cv-path "C:/path/to/cv.pdf" --pause-each-step --slow-mo-ms 700
```

Defaults:

- Keywords: `AI`
- Location: `London`
- Distance: `Within 50 miles`
- Industries: `Select All`
- Posted: `Within 7 days`
- Job Type: `Any`
- Remote only: unchecked
- Submit: false

## Target A Specific Job

```powershell
python backend/scripts/run_jobserve_apply_debug.py --email "me@example.com" --cv-path "C:/path/to/cv.pdf" --target-title "AI Engineer" --target-company "Example Ltd" --target-reference "ABC123" --pause-each-step
```

If no target title, company, or reference is provided, the script selects the first suitable result for local testing.

## Submit Mode

Only use this when you intentionally want to send the application.

```powershell
python backend/scripts/run_jobserve_apply_debug.py --email "me@example.com" --cv-path "C:/path/to/cv.pdf" --submit --pause-each-step
```

In submit mode the script clicks the final JobServe Apply button, waits for `Your application has been submitted.`, turns off the Job Seeker account options when present, and closes the modal.

## Artifacts

Videos are saved under:

```text
backend/runtime/jobserve-debug/videos/
```

Playwright traces are saved under:

```text
backend/runtime/jobserve-debug/traces/
```

Open a trace with:

```powershell
python -m playwright show-trace backend/runtime/jobserve-debug/traces/<trace-file>.zip
```

The script also writes the usual apply debug screenshots and HTML snapshots through the production debug recorder.
