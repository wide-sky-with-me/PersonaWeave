# Replace the verified app-local VC runtime while the application's Python is stopped.
# Using Windows PowerShell avoids locking the very DLLs being repaired with Python itself.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$root = Split-Path -Parent $PSScriptRoot
$names = @('msvcp140.dll', 'msvcp140_1.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll')
$lock = $null
try {
    $manifestPath = Join-Path $root 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.edition -notin @('windows-x64-cpu', 'windows-x64-gpu')) { throw '不支持此程序版本。' }
    $hashes = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'vc-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    [IO.Directory]::CreateDirectory((Join-Path $root 'data')) | Out-Null
    $lock = [IO.File]::Open((Join-Path $root 'data\portable.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
    try { $lock.Lock(0, 1) } catch { throw '请先在程序窗口按 Ctrl+C 关闭后端，保留网页，再执行修复。' }
    foreach ($name in $names) {
        $source = Join-Path $PSScriptRoot ('vc-runtime\' + $name)
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hashes.$name) {
            throw ('修复文件校验失败：' + $name)
        }
        $target = Join-Path $root ('runtime\' + $name)
        $old = $manifest.files.('runtime/' + $name)
        if (Test-Path -LiteralPath $target) {
            $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $old -and $actual -ne $hashes.$name) { throw ('程序文件被修改，未覆盖：' + $name) }
        } elseif ($old) { throw ('原包文件缺失，请先核对安装目录：' + $name) }
    }
    $backup = Join-Path $root ('data\support_backups\vc-runtime-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $backup 'manifest.json')
    foreach ($name in $names) {
        $target = Join-Path $root ('runtime\' + $name)
        if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination (Join-Path $backup $name) }
    }
    foreach ($name in $names) {
        $target = Join-Path $root ('runtime\' + $name)
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot ('vc-runtime\' + $name)) -Destination ($target + '.repair-tmp')
        Move-Item -LiteralPath ($target + '.repair-tmp') -Destination $target -Force
        $manifest.files | Add-Member -MemberType NoteProperty -Name ('runtime/' + $name) -Value $hashes.$name -Force
    }
    $json = $manifest | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText(($manifestPath + '.repair-tmp'), $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath ($manifestPath + '.repair-tmp') -Destination $manifestPath -Force
    Write-Host 'VC++ 运行库修复完成，原文件已备份。请双击 Start.cmd，回到原网页先保存工程。'
} catch {
    Write-Host ('修复未完成：' + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
