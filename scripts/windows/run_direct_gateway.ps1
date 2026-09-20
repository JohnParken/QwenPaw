# Point the personal QwenPaw TL provider straight at a real internal chatbbc
# gateway and start the app, without the local Node TL proxy.
#
# Run from anywhere:
#   pwsh -File scripts/windows/run_direct_gateway.ps1 `
#     -BaseUrl http://10.1.2.3:8080 -AppId my-app -TrCode agent-chat
#
# The script probes <BaseUrl>/chatbbc/init_session and <BaseUrl>/chatbbc/chat
# before writing anything, so a wrong address is reported here instead of
# surfacing as a failed model call in the Console.

[CmdletBinding()]
param(
  # Gateway service root over plain http, for example http://10.1.2.3:8080 or
  # http://gateway.corp.example/company. Trailing slashes are stripped.
  [Parameter(Mandatory = $true)][string]$BaseUrl,

  # Business metadata sent as appId / trCode / trVersion. These are not
  # credentials; leave them empty when the gateway does not require them.
  [string]$AppId = "",
  [string]$TrCode = "",
  [string]$TrVersion = "",

  # Local label for the single TL route. It is not sent to the gateway and
  # does not select an upstream model.
  [string]$Model = "internal-route",

  # Console context window in tokens for conservative local budgeting.
  [int]$MaxInputLength = 32768,

  # Print the resolved tl-provider.json path and exit after probing.
  [switch]$ProbeOnly,

  # Rewrite tl-provider.json even when it already exists.
  [switch]$Force
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host "[direct-tl] $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
  Write-Host "[direct-tl] ERROR: $Message" -ForegroundColor Red
  exit 1
}

# ---------------------------------------------------------------- validation
$BaseUrl = $BaseUrl.Trim().TrimEnd("/")
if ($BaseUrl -notmatch '^https?://[^/?#]+') {
  Fail "-BaseUrl must be an absolute http(s) service root, got '$BaseUrl'."
}
if ($BaseUrl -match '\?|#') {
  Fail "-BaseUrl must not contain a query string or fragment."
}
if ($BaseUrl -match '/v1$' -or $BaseUrl -match '/chat/completions$') {
  Fail "-BaseUrl must be the TL service root, not an OpenAI-compatible path. Remove '/v1'."
}
$GatewayHost = ([System.Uri]$BaseUrl).Host

# ------------------------------------------------------- bypass forward proxy
# The gateway is a private endpoint over plain http: a forward proxy would
# fail, rewrite, or disclose the request. Clear the variables for this process
# and opt the gateway host out of any proxy configuration.
foreach ($name in @(
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy"
  )) {
  Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$existingNoProxy = [Environment]::GetEnvironmentVariable("NO_PROXY")
$noProxyEntries = @()
if ($existingNoProxy) { $noProxyEntries += $existingNoProxy }
$noProxyEntries += $GatewayHost
[Environment]::SetEnvironmentVariable("NO_PROXY", ($noProxyEntries -join ","))
[Environment]::SetEnvironmentVariable("no_proxy", ($noProxyEntries -join ","))
Write-Step "Direct connection to $GatewayHost (environment proxies cleared)."

# --------------------------------------------------------------- probe first
$initBody = @{
  appId     = $AppId
  trCode    = $TrCode
  trVersion = $TrVersion
  timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  requestId = [guid]::NewGuid().ToString()
  data      = @{
    prompt_variables = @(
      @{ name = "system_prompt"; value = "Reply with OK." }
    )
  }
} | ConvertTo-Json -Depth 6 -Compress

Write-Step "POST $BaseUrl/chatbbc/init_session"
try {
  $initResponse = Invoke-RestMethod -Method Post -Uri "$BaseUrl/chatbbc/init_session" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($initBody)) `
    -TimeoutSec 30
} catch {
  Fail "init_session is unreachable: $($_.Exception.Message)"
}

$initPayload = @($initResponse)[0]
$initPreview = ($initPayload | ConvertTo-Json -Depth 6 -Compress)
if ($initPreview.Length -gt 500) { $initPreview = $initPreview.Substring(0, 500) + "..." }
Write-Host "    response: $initPreview"

if ($initPayload.code -ne 0) {
  Fail "init_session returned business code '$($initPayload.code)'. The TL client requires numeric code 0."
}
$sessionId = $initPayload.data.session_id
if (-not $sessionId) {
  Fail "init_session response has no data.session_id. Check that this is the chatbbc gateway and not an OpenAI-compatible endpoint."
}
Write-Step "init_session OK, session_id = $sessionId"

$chatBody = @{
  appId     = $AppId
  trCode    = $TrCode
  trVersion = $TrVersion
  timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  requestId = [guid]::NewGuid().ToString()
  data      = @{
    session_id = $sessionId
    txt        = '{"version":1,"messages":[{"role":"user","content":"Reply OK"}]}'
    files      = @()
    stream     = $false
  }
} | ConvertTo-Json -Depth 6 -Compress

Write-Step "POST $BaseUrl/chatbbc/chat (stream=false)"
try {
  $chatResponse = Invoke-RestMethod -Method Post -Uri "$BaseUrl/chatbbc/chat" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($chatBody)) `
    -TimeoutSec 120
} catch {
  Fail "chat is unreachable: $($_.Exception.Message)"
}

$chatPayload = @($chatResponse)[0]
$chatPreview = ($chatPayload | ConvertTo-Json -Depth 6 -Compress)
if ($chatPreview.Length -gt 500) { $chatPreview = $chatPreview.Substring(0, 500) + "..." }
Write-Host "    response: $chatPreview"

if ($chatPayload.code -ne 0) {
  Fail "chat returned business code '$($chatPayload.code)'. The TL client requires numeric code 0."
}
if ($null -eq $chatPayload.data.txt) {
  Fail "chat response has no data.txt. The TL client reads the model body from data.txt only."
}
Write-Step "chat OK, model text length = $($chatPayload.data.txt.Length)"

# ----------------------------------------------------------- write the config
$workingDir = $env:QWENPAW_WORKING_DIR
if (-not $workingDir) { $workingDir = Join-Path $env:USERPROFILE ".qwenpaw" }
$workingDir = [System.IO.Path]::GetFullPath($workingDir)
$configPath = Join-Path $workingDir "tl-provider.json"

$config = [ordered]@{
  version      = 1
  enabled      = $true
  providers    = @(
    [ordered]@{
      id         = "tl-gateway"
      name       = "TL Gateway"
      base_url   = $BaseUrl
      chat_model = "TLChatModel"
      is_custom  = $true
      models     = @(
        [ordered]@{
          id                         = $Model
          name                       = $Model
          max_input_length           = $MaxInputLength
          max_input_length_configured = $true
        }
      )
      tl_config  = [ordered]@{
        app_id                       = $AppId
        tr_code                      = $TrCode
        tr_version                   = $TrVersion
        system_prompt_variable_name  = "system_prompt"
        tool_calling_mode            = "system_prompt"
        json_correction_max_attempts = 1
        trust_env                    = $false
        timeout_seconds              = 150.0
        stream_idle_timeout_seconds  = 0.0
        max_request_bytes            = 1048576
        max_response_bytes           = 4194304
        max_wire_response_bytes      = 67108864
        max_sse_event_bytes          = 1048576
      }
    }
  )
  active_model = [ordered]@{
    provider_id = "tl-gateway"
    model       = $Model
  }
}

Write-Step "Config path: $configPath"
if ((Test-Path $configPath) -and -not $Force) {
  Write-Host "    A tl-provider.json already exists; leaving it untouched." -ForegroundColor Yellow
  Write-Host "    Re-run with -Force to overwrite it with the probed gateway." -ForegroundColor Yellow
} else {
  if (-not (Test-Path $workingDir)) {
    New-Item -ItemType Directory -Force -Path $workingDir | Out-Null
  }
  $json = $config | ConvertTo-Json -Depth 8
  # UTF-8 without BOM: the Python loader opens the file with encoding="utf-8".
  [System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding($false)))
  Write-Step "Wrote tl-provider.json for provider 'tl-gateway'."
}

if ($ProbeOnly) {
  Write-Step "Probe-only run finished."
  exit 0
}

# ------------------------------------------------------------------ start app
$repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
Set-Location $repoRoot
Write-Step "Starting 'qwenpaw app' from $repoRoot"
Write-Host "    Console: http://127.0.0.1:8088/" -ForegroundColor Green
Write-Host "    Debug wire logs: qwenpaw app --log-level debug" -ForegroundColor DarkGray
qwenpaw app
exit $LASTEXITCODE
