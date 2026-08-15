param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments = @())
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($Arguments -join ' ')"
    }
}

if ([string]::IsNullOrWhiteSpace($env:PRODUCTION_BASE_URL)) {
    throw "PRODUCTION_BASE_URL is required."
}

$env:CUTOVER_ROOT = $Root

$code = @'
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

base = (os.environ.get("PRODUCTION_BASE_URL") or "").rstrip("/")
root = Path(os.environ["CUTOVER_ROOT"])
report_dir = root / ".localstate" / "postgres_cutover_reports"
report_dir.mkdir(parents=True, exist_ok=True)
report_path = report_dir / f"postgres-post-cutover-validation-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"
lines = []
failed = False

def log(message):
    print(message)
    lines.append(message)

def request(method, path, data=None, expected=(200,), allow_redirect=True):
    global failed
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler if allow_redirect else NoRedirect)
    try:
        resp = opener.open(req, timeout=20)
        status = resp.getcode()
        text = resp.read(500).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        text = exc.read(500).decode("utf-8", "replace")
    except Exception as exc:
        failed = True
        log(f"FAIL {method} {path}: {type(exc).__name__}: {exc}")
        return
    if status == 500:
        failed = True
        log(f"FAIL {method} {path}: HTTP 500 {text}")
    elif status in expected:
        log(f"PASS {method} {path}: HTTP {status}")
    elif status in {301, 302, 303, 307, 308, 401, 403} and allow_redirect:
        log(f"AUTH/REDIRECT {method} {path}: HTTP {status}")
    else:
        failed = True
        log(f"FAIL {method} {path}: HTTP {status} {text}")

request("GET", "/")
request("GET", "/estimator")
request("GET", "/login")
request("GET", "/pro/dashboard", expected=(200, 303, 307, 401, 403), allow_redirect=True)
request("GET", "/pro/customers", expected=(200, 303, 307, 401, 403), allow_redirect=True)
request("GET", "/api/services/suspension")
request("GET", "/api/service/ball_joint_replacement_each")
request("GET", "/api/parts-sources?year=2001&make=Mercedes-Benz&model=E320&service=ball_joint_replacement_each")
request("POST", "/estimate", {
    "year": 2001,
    "make": "Mercedes-Benz",
    "model": "E320",
    "displayModel": "E320",
    "category": "suspension",
    "serviceCode": "ball_joint_replacement_each",
    "service": "Ball Joint Replacement (each)",
    "laborHours": 2.0,
    "partsPrice": 120.0,
    "laborRate": 125.0,
    "zip": "90210"
})

log("Railway log check skipped unless a safe Railway CLI command is already available and approved.")
log("FAIL summary present." if failed else "PASS: post-cutover validation completed without HTTP 500.")
report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Validation report: {report_path}")
if failed:
    raise SystemExit(1)
'@

$helper = Join-Path $Root ".localstate\postgres_post_cutover_validation.py"
Set-Content -LiteralPath $helper -Value $code -Encoding UTF8
Invoke-Native $python @("-m", "py_compile", $helper)
Invoke-Native $python @($helper)
