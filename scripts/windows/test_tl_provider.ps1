# End-to-end check for a real TL (chatbbc) provider behind `qwenpaw app`.
#
#   pwsh -File scripts\windows\test_tl_provider.ps1 -ProviderId tl-gateway -Model internal-route
#
# Runs, in order:
#   1. GET  /api/healthz                      (app must be ready)
#   2. GET  /api/models                       (find the TL provider)
#   3. POST /api/models/{id}/test             -> init_session only  (verification=provider_only)
#   4. POST /api/models/{id}/models/test      -> init + chat        (verification=live)
#   5. POST /api/console/chat                 -> real agent turn via model_slot_override
#                                               (does NOT change the saved default model)
#
# -SkipChat omits step 5. Exit code 0 only when every requested step passed.

[CmdletBinding()]
param(
  [string]$BaseUrl = "http://127.0.0.1:8088",
  [Parameter(Mandatory = $true)][string]$ProviderId,
  [string]$Model = "",
  [string]$Message = "Reply with exactly: TL OK",
  [int]$TimeoutSec = 300,
  [switch]$SkipChat
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")
$script:Failed = 0

function Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Pass([string]$Text) { Write-Host "   PASS  $Text" -ForegroundColor Green }
function Fail([string]$Text) { Write-Host "   FAIL  $Text" -ForegroundColor Red; $script:Failed++ }
function Note([string]$Text) { Write-Host "   note  $Text" -ForegroundColor DarkGray }

# JSON helpers that work on both Windows PowerShell 5.1 and PowerShell 7.
function ConvertTo-Utf8Json([string]$Json) {
  [System.Text.Encoding]::UTF8.GetBytes($Json)
}

function Invoke-Json {
  param(
    [string]$Method,
    [string]$Uri,
    [string]$Json = $null,
    [hashtable]$Headers = @{},
    [int]$Timeout = 30
  )
  $iwrArgs = @{
    Uri             = $Uri
    Method          = $Method
    Headers         = $Headers
    UseBasicParsing = $true
    TimeoutSec      = $Timeout
  }
  if ($null -ne $Json) {
    $iwrArgs["ContentType"] = "application/json; charset=utf-8"
    $iwrArgs["Body"] = ConvertTo-Utf8Json $Json
  }
  $response = Invoke-WebRequest @iwrArgs
  $text = $response.Content
  if ($text -is [byte[]]) { $text = [System.Text.Encoding]::UTF8.GetString($text) }
  if ([string]::IsNullOrWhiteSpace($text)) { return $null }
  return $text | ConvertFrom-Json
}

# ---------------------------------------------------------------- 1. readiness
Step "1/5 readiness  GET $BaseUrl/api/healthz"
try {
  $health = Invoke-Json -Method Get -Uri "$BaseUrl/api/healthz" -Timeout 15
  Pass "healthz status=$($health.status) uptime=$($health.uptime_seconds)s"
} catch {
  Fail "healthz: $($_.Exception.Message)"
  Note "503 starting => app still booting; connection refused => 'qwenpaw app' is not running."
  exit 1
}

# ------------------------------------------------------------ 2. provider info
Step "2/5 provider    GET $BaseUrl/api/models"
try {
  $providers = @(Invoke-Json -Method Get -Uri "$BaseUrl/api/models" -Timeout 30)
} catch {
  Fail "list providers: $($_.Exception.Message)"
  exit 1
}
$provider = $providers | Where-Object { $_.id -eq $ProviderId } | Select-Object -First 1
if (-not $provider) {
  Fail "provider '$ProviderId' not found. Available: $((($providers | ForEach-Object { $_.id }) -join ', '))"
  exit 1
}
if ($provider.chat_model -ne "TLChatModel") {
  Fail "provider '$ProviderId' uses chat_model='$($provider.chat_model)', not TLChatModel."
  exit 1
}
# A model created through POST /api/models/custom-providers lands in
# extra_models; one from tl-provider.json lands in models. Accept both.
$modelIds = @()
foreach ($collection in @($provider.models, $provider.extra_models)) {
  if ($collection) { $modelIds += @($collection | ForEach-Object { $_.id }) }
}
if (-not $Model -and $modelIds.Count -gt 0) { $Model = $modelIds[0] }
if (-not $Model) { Fail "provider '$ProviderId' has no model label configured."; exit 1 }
Pass "chat_model=$($provider.chat_model) base_url=$($provider.base_url) model=$Model"
Note "trust_env=$($provider.tl_config.trust_env) timeout=$($provider.tl_config.timeout_seconds)s appId='$($provider.tl_config.app_id)' trCode='$($provider.tl_config.tr_code)'"

# ---------------------------------------------------- 3. init_session only
Step "3/5 init        POST /api/models/$ProviderId/test"
try {
  $init = Invoke-Json -Method Post -Uri "$BaseUrl/api/models/$ProviderId/test" -Json "{}" -Timeout 60
  if ($init.success) {
    Pass "init_session OK verification=$($init.verification) :: $($init.message)"
  } else {
    Fail "init_session: $($init.message)"
    Note "connect timeout => wrong address/VPN; 407/502 => forward proxy; code!=0 => appId/trCode/trVersion."
  }
} catch {
  $detail = $_.ErrorDetails.Message
  Fail "init_session request: $($_.Exception.Message) $detail"
}

# ------------------------------------------------------- 4. init + real chat
Step "4/5 chat        POST /api/models/$ProviderId/models/test"
try {
  $live = Invoke-Json -Method Post -Uri "$BaseUrl/api/models/$ProviderId/models/test" `
    -Json (@{ model_id = $Model } | ConvertTo-Json -Compress) -Timeout 180
  if ($live.success -and $live.verification -eq "live") {
    Pass "TL route completed chat (verification=live)"
  } elseif ($live.success) {
    Fail "provider reachable but verification='$($live.verification)' (expected 'live'); message=$($live.message)"
  } else {
    Fail "chat: $($live.message) (http_status=$($live.http_status) error_kind=$($live.error_kind))"
  }
} catch {
  Fail "model test request: $($_.Exception.Message) $($_.ErrorDetails.Message)"
}

if ($SkipChat) {
  Write-Host ""
  if ($script:Failed -gt 0) { Write-Host "$($script:Failed) step(s) failed." -ForegroundColor Red; exit 1 }
  Write-Host "TL provider checks passed (chat turn skipped)." -ForegroundColor Green
  exit 0
}

# --------------------------------- 5. real agent turn through the live console
Step "5/5 agent turn  POST /api/console/chat (model_slot_override=$ProviderId:$Model)"
$sessionId = "tl-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$body = @{
  input      = @(@{ role = "user"; content = $Message })
  session_id = $sessionId
  user_id    = "tl-check"
  stream     = $true
  model_slot_override = @{ provider_id = $ProviderId; model = $Model }
} | ConvertTo-Json -Depth 6 -Compress

try {
  $resp = Invoke-WebRequest -Uri "$BaseUrl/api/console/chat" -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Headers @{ Accept = "text/event-stream" } `
    -Body (ConvertTo-Utf8Json $body) `
    -UseBasicParsing -TimeoutSec $TimeoutSec
  $sse = $resp.Content
  if ($sse -is [byte[]]) { $sse = [System.Text.Encoding]::UTF8.GetString($sse) }
} catch {
  Fail "console chat request: $($_.Exception.Message) $($_.ErrorDetails.Message)"
  exit 1
}

$text = New-Object System.Text.StringBuilder
$errors = @()
foreach ($line in ($sse -split "`r?`n")) {
  if (-not $line.StartsWith("data:")) { continue }
  $payload = $line.Substring(5).Trim()
  if (-not $payload) { continue }
  try { $event = $payload | ConvertFrom-Json } catch { continue }
  if ($event.PSObject.Properties.Name -contains "error" -and $event.error) {
    $errors += [string]$event.error
  }
  if ($event.object -eq "content" -and $event.delta -and $event.text) {
    [void]$text.Append([string]$event.text)
  }
}

if ($errors.Count -gt 0) {
  Fail "run reported an error: $($errors -join ' | ')"
} elseif ($text.Length -eq 0) {
  Fail "SSE stream carried no content deltas (check qwenpaw app --log-level debug for TL_WIRE)."
} else {
  Pass "agent answered through the TL provider:"
  Write-Host "   > $($text.ToString().Trim())" -ForegroundColor White
}

Write-Host ""
if ($script:Failed -gt 0) {
  Write-Host "$($script:Failed) check(s) failed." -ForegroundColor Red
  exit 1
}
Write-Host "Real TL provider verified: init_session + chat + full agent turn." -ForegroundColor Green
exit 0
