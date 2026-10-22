# Screenshot (whole screen or a rectangle) -> text, with the OCR built into Windows.
# Called by desktop.py (Jarvis, "Was steht auf meinem Bildschirm?"). The image is a
# temporary file that is deleted at the end; nothing leaves this PC.
# -ImagePath reads an existing image instead (used by the tests).
param([int]$Left = -1, [int]$Top = 0, [int]$Width = 0, [int]$Height = 0, [string]$ImagePath = "")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime

if ($ImagePath) {
    $tmp = $ImagePath
} else {
if ($Left -lt 0) {
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $Left, $Top, $Width, $Height = $b.Left, $b.Top, $b.Width, $b.Height
}
$tmp = Join-Path $env:TEMP ("jarvis-ocr-" + [guid]::NewGuid().ToString("N") + ".png")
$bmp = New-Object System.Drawing.Bitmap $Width, $Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
try {
    $g.CopyFromScreen($Left, $Top, 0, 0, $bmp.Size)
    $bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
} finally { $g.Dispose(); $bmp.Dispose() }
}

try {
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function Await($op, [Type]$type) {
        $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result
    }
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) { throw "Keine OCR-Sprache installiert" }
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $result.Lines | ForEach-Object { $_.Text }
    $stream.Dispose()
} finally {
    if (-not $ImagePath) { Remove-Item $tmp -ErrorAction SilentlyContinue }
}
