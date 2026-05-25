# Build a clean circular ICO from rage_icon.png
# - Crops to square
# - Masks corners + the bottom-right emoji square to transparent
# - Resamples to 256/128/64/48/32/24/16
# - Packs into a multi-resolution .ico file

Add-Type -AssemblyName System.Drawing

$src = "C:\Users\prkid\Documents\macro_recorder\QytsCreations\assets\rage_icon.png"
$out = "C:\Users\prkid\Documents\macro_recorder\QytsCreations_CS\Resources\icon.ico"

# Load source
$srcImg = [System.Drawing.Image]::FromFile($src)
$w = $srcImg.Width
$h = $srcImg.Height

# Center-crop to square
$size = [Math]::Min($w, $h)
$srcX = [int](($w - $size) / 2)
$srcY = [int](($h - $size) / 2)

# Render to a high-res square canvas with circular alpha mask
$canvasSize = 256
$canvas = New-Object System.Drawing.Bitmap $canvasSize, $canvasSize, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g = [System.Drawing.Graphics]::FromImage($canvas)
$g.SmoothingMode    = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.Clear([System.Drawing.Color]::Transparent)

# Clip to circle so corners (and the embedded emoji square) get cut off
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddEllipse(0, 0, $canvasSize, $canvasSize)
$g.SetClip($path)
$g.DrawImage($srcImg, (New-Object System.Drawing.Rectangle 0, 0, $canvasSize, $canvasSize),
             $srcX, $srcY, $size, $size,
             [System.Drawing.GraphicsUnit]::Pixel)
$g.ResetClip()
$g.Dispose()
$srcImg.Dispose()

# Now generate sized bitmaps and pack into ICO format
$sizes = @(256, 128, 64, 48, 32, 24, 16)
$pngStreams = @()
foreach ($s in $sizes)
{
    $bmp = New-Object System.Drawing.Bitmap $s, $s, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $bg = [System.Drawing.Graphics]::FromImage($bmp)
    $bg.SmoothingMode    = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $bg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $bg.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $bg.Clear([System.Drawing.Color]::Transparent)
    $bg.DrawImage($canvas, 0, 0, $s, $s)
    $bg.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngStreams += ,@($s, $ms.ToArray())
    $bmp.Dispose()
}
$canvas.Dispose()

# Write ICO: 6-byte header + 16-byte ICONDIRENTRY per image + PNG payloads
$fs = [System.IO.File]::Open($out, [System.IO.FileMode]::Create)
$bw = New-Object System.IO.BinaryWriter $fs
$bw.Write([UInt16]0)            # reserved
$bw.Write([UInt16]1)            # type = 1 (icon)
$bw.Write([UInt16]$pngStreams.Count)

$headerSize = 6 + (16 * $pngStreams.Count)
$offset = $headerSize
foreach ($entry in $pngStreams)
{
    $s = $entry[0]; $data = $entry[1]
    $w = if ($s -ge 256) { 0 } else { [byte]$s }
    $h = if ($s -ge 256) { 0 } else { [byte]$s }
    $bw.Write([byte]$w)         # width  (0 = 256)
    $bw.Write([byte]$h)         # height (0 = 256)
    $bw.Write([byte]0)          # palette
    $bw.Write([byte]0)          # reserved
    $bw.Write([UInt16]1)        # planes
    $bw.Write([UInt16]32)       # bpp
    $bw.Write([UInt32]$data.Length)
    $bw.Write([UInt32]$offset)
    $offset += $data.Length
}
foreach ($entry in $pngStreams) { $bw.Write($entry[1]) }
$bw.Close(); $fs.Close()

"ICO written: $out ($(((Get-Item $out).Length / 1KB).ToString('0.0')) KB)"

# Also save a PNG version next to it for in-window display
$pngOut = "C:\Users\prkid\Documents\macro_recorder\QytsCreations_CS\Resources\icon.png"
$pngImg = New-Object System.Drawing.Bitmap 256, 256, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$pg = [System.Drawing.Graphics]::FromImage($pngImg)
$pg.SmoothingMode    = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$pg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$pg.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$pg.Clear([System.Drawing.Color]::Transparent)
# Re-render circle from source for PNG
$srcImg = [System.Drawing.Image]::FromFile($src)
$path2 = New-Object System.Drawing.Drawing2D.GraphicsPath
$path2.AddEllipse(0, 0, 256, 256)
$pg.SetClip($path2)
$pg.DrawImage($srcImg, (New-Object System.Drawing.Rectangle 0, 0, 256, 256),
             $srcX, $srcY, $size, $size, [System.Drawing.GraphicsUnit]::Pixel)
$pg.ResetClip()
$pg.Dispose()
$srcImg.Dispose()
$pngImg.Save($pngOut, [System.Drawing.Imaging.ImageFormat]::Png)
$pngImg.Dispose()
"PNG written: $pngOut"
