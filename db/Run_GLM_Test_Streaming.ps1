# Streaming diagnostic version - watch tokens arrive live so you can SEE
# whether GLM is still working instead of guessing.
# Ollama must be running on http://localhost:11434

param(
    [string]$PromptFile = ".\GLM_Test_Prompt_Small.txt"
)

$prompt = [string](Get-Content $PromptFile -Raw)

$body = @{
    model  = "glm-film-director"
    prompt = $prompt
    stream = $true
} | ConvertTo-Json -Depth 10 -Compress

$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

Write-Host "Streaming from GLM (prompt: $PromptFile)..."
Write-Host "---"

$fullText = ""

try {
    $webRequest = [System.Net.HttpWebRequest]::Create("http://localhost:11434/api/generate")
    $webRequest.Method = "POST"
    $webRequest.ContentType = "application/json; charset=utf-8"
    $webRequest.ContentLength = $bodyBytes.Length
    $requestStream = $webRequest.GetRequestStream()
    $requestStream.Write($bodyBytes, 0, $bodyBytes.Length)
    $requestStream.Close()

    $webResponse = $webRequest.GetResponse()
    $responseStream = $webResponse.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($responseStream)

    while (-not $reader.EndOfStream) {
        $line = $reader.ReadLine()
        if ($line) {
            $chunk = $line | ConvertFrom-Json
            if ($chunk.response) {
                Write-Host -NoNewline $chunk.response
                $fullText += $chunk.response
            }
            if ($chunk.done -eq $true) {
                Write-Host ""
                Write-Host "---"
                Write-Host "DONE. Total eval duration: $([math]::Round($chunk.eval_duration / 1e9, 1))s, tokens: $($chunk.eval_count)"
            }
        }
    }
    $reader.Close()
}
catch {
    Write-Host ""
    Write-Host "STREAMING FAILED:"
    Write-Host $_.Exception.Message
    exit 1
}

if (-not (Test-Path ".\outputs")) {
    New-Item -ItemType Directory -Path ".\outputs" | Out-Null
}
$fullText | Set-Content ".\outputs\GLM_Stream_Output.json" -Encoding UTF8
Write-Host "Saved raw text to .\outputs\GLM_Stream_Output.json"
