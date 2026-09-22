# Run the GLM production test, validate the schema contract, and save the result as JSON.
# Ollama must be running on http://localhost:11434

param(
    [string]$PromptFile = ".\GLM_Test_Prompt.txt"
)

$prompt = [string](Get-Content $PromptFile -Raw)

# NOTE: format:"json" deliberately omitted. Grammar-constrained decoding under
# format:"json" is dramatically slower for large nested outputs and is what
# was causing multi-minute "hangs". The Modelfile's system prompt already
# reliably produces clean JSON on its own (confirmed via `ollama run`), so we
# rely on that and just validate/extract on the client side instead.
$body = @{
    model  = "glm-film-director"
    prompt = $prompt
    stream = $false
} | ConvertTo-Json -Depth 10 -Compress

# --- Sanity check the JSON locally BEFORE sending it anywhere ---
# If this fails, the bug is in how we built the string, not in Ollama.
try {
    $null = $body | ConvertFrom-Json
}
catch {
    Write-Host ""
    Write-Host "LOCAL JSON BUILD IS BROKEN (this is the real bug, not Ollama):"
    Write-Host $_.Exception.Message
    $body | Set-Content ".\Request_Debug.json" -Encoding UTF8
    Write-Host "Raw body saved to .\Request_Debug.json for inspection."
    exit 1
}

# --- Send as explicit UTF-8 bytes ---
# Invoke-RestMethod on Windows PowerShell 5.1 can silently mis-encode a plain
# string body (legacy codepage instead of UTF-8), which corrupts any non-ASCII
# character (accented names, em dashes, etc.) and can break the JSON structure
# in a completely unrelated place. Encoding explicitly avoids that permanently.
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

Write-Host "Sending test to GLM..."

try {
    $response = Invoke-RestMethod `
        -Uri "http://localhost:11434/api/generate" `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body $bodyBytes
}
catch {
    Write-Host ""
    Write-Host "REQUEST FAILED:"

    # Ollama always puts the real reason in the response body — surface it
    # instead of just the HTTP status code.
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        Write-Host "Ollama says: $($_.ErrorDetails.Message)"
    }
    elseif ($_.Exception.Response) {
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $errorBody = $reader.ReadToEnd()
            Write-Host "Ollama says: $errorBody"
        }
        catch {
            Write-Host $_.Exception.Message
        }
    }
    else {
        Write-Host $_.Exception.Message
    }
    exit 1
}

if (-not $response.response) {
    Write-Host "Ollama returned no response body."
    exit 1
}

try {
    $raw = $response.response.Trim()

    # Defensive extraction: without format:"json" enforcing grammar, GLM
    # *should* still return clean JSON (per its system prompt), but strip
    # markdown fences or stray text around it just in case.
    if ($raw -match '```json') {
        $raw = ($raw -split '```json')[1] -split '```' | Select-Object -First 1
    }
    elseif ($raw -match '```') {
        $raw = ($raw -split '```')[1] -split '```' | Select-Object -First 1
    }

    $firstBrace = $raw.IndexOf("{")
    $lastBrace = $raw.LastIndexOf("}")
    if ($firstBrace -ge 0 -and $lastBrace -gt $firstBrace) {
        $raw = $raw.Substring($firstBrace, $lastBrace - $firstBrace + 1)
    }

    $result = $raw | ConvertFrom-Json
}
catch {
    Write-Host ""
    Write-Host "GLM output was not valid JSON:"
    Write-Host $response.response
    exit 1
}

# --- Schema contract check: verify expected top-level keys are present ---
$expectedKeys = @("stage", "project", "characters", "locations", "episodes")
$missing = @()
foreach ($key in $expectedKeys) {
    if (-not ($result.PSObject.Properties.Name -contains $key)) {
        $missing += $key
    }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "SCHEMA WARNING: missing expected top-level keys: $($missing -join ', ')"
    Write-Host "Saving output anyway for inspection."
}
else {
    Write-Host ""
    Write-Host "Schema check passed: stage/project/characters/locations/episodes all present."
}

if (-not (Test-Path ".\outputs")) {
    New-Item -ItemType Directory -Path ".\outputs" | Out-Null
}

$jsonOut = $result | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText(
    (Join-Path (Get-Location) "outputs\GLM_Test_Output.json"),
    $jsonOut,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ""
Write-Host "Stage returned: $($result.stage)"
Write-Host "Characters returned: $($result.characters.Count)"
Write-Host "Locations returned: $($result.locations.Count)"
Write-Host "Episodes returned: $($result.episodes.Count)"
Write-Host "Saved: .\outputs\GLM_Test_Output.json"
