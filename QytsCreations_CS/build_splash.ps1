# Build splash screen PNG: dark blue tech background + grid + RAGE icon + tagline
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Drawing.Drawing2D

$W = 720
$H = 420
$bmp = New-Object System.Drawing.Bitmap $W, $H, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

# â”€â”€ 1. Background: radial-ish gradient via linear, deep blue Catppuccin
$bgRect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
$grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush $bgRect, ([System.Drawing.Color]::FromArgb(30, 30, 46)), ([System.Drawing.Color]::FromArgb(15, 15, 25)), 135.0
$g.FillRectangle($grad, $bgRect)
$grad.Dispose()

# â”€â”€ 2. Tech grid: faint blue diagonal + grid lines
$gridPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(40, 137, 180, 250)), 1
for ($i = -200; $i -lt ($W + 200); $i += 40)
{
    $g.DrawLine($gridPen, $i, 0, ($i + $H), $H)         # diagonal lines
}
$gridPen.Dispose()

# Horizontal scanlines
$scanPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(25, 137, 180, 250)), 1
for ($y = 0; $y -lt $H; $y += 18) { $g.DrawLine($scanPen, 0, $y, $W, $y) }
$scanPen.Dispose()

# â”€â”€ 3. Glowing corner accents (cyan + mauve hex)
function Draw-Hex {
    param($cx, $cy, $r, $color, $alpha)
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alpha, $color.R, $color.G, $color.B))
    $pts = New-Object System.Drawing.PointF[] 6
    for ($k = 0; $k -lt 6; $k++) {
        $a = $k * [Math]::PI / 3
        $pts[$k] = New-Object System.Drawing.PointF (($cx + $r * [Math]::Cos($a)), ($cy + $r * [Math]::Sin($a)))
    }
    $g.FillPolygon($brush, $pts)
    $brush.Dispose()
}
$cyan  = [System.Drawing.Color]::FromArgb(255, 148, 226, 213)
$mauve = [System.Drawing.Color]::FromArgb(255, 203, 166, 247)
$blue  = [System.Drawing.Color]::FromArgb(255, 137, 180, 250)
Draw-Hex 60   60   28 $cyan  35
Draw-Hex 95   95   18 $cyan  55
Draw-Hex 40   110  12 $mauve 70
Draw-Hex ($W-60)   ($H-60)   28 $mauve 35
Draw-Hex ($W-95)   ($H-95)   18 $mauve 55
Draw-Hex ($W-40)   ($H-110)  12 $cyan  70

# â”€â”€ 4. Embed the RAGE icon (circular, already masked)
$iconPath = "C:\Users\prkid\Documents\macro_recorder\QytsCreations_CS\Resources\icon.png"
if (Test-Path $iconPath)
{
    $icon = [System.Drawing.Image]::FromFile($iconPath)
    $iconSize = 140
    $ix = ($W - $iconSize) / 2
    $iy = 70
    # Soft glow underneath
    for ($r = 30; $r -gt 0; $r--)
    {
        $glowBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(([int](70 - $r * 2)), 250, 100, 130))
        $g.FillEllipse($glowBrush, ($ix - $r), ($iy - $r), ($iconSize + $r * 2), ($iconSize + $r * 2))
        $glowBrush.Dispose()
    }
    $g.DrawImage($icon, $ix, $iy, $iconSize, $iconSize)
    $icon.Dispose()
}

# â”€â”€ 5. App name "QytCroRec" in bold cyan
$titleFont = New-Object System.Drawing.Font "Segoe UI", 36, ([System.Drawing.FontStyle]::Bold)
$titleBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 137, 180, 250))
$titleStr   = "QytCroRec"
$titleSize  = $g.MeasureString($titleStr, $titleFont)
$tx = ($W - $titleSize.Width) / 2
$ty = 225
# Shadow
$shadowBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(160, 0, 0, 0))
$g.DrawString($titleStr, $titleFont, $shadowBrush, ($tx + 2), ($ty + 2))
$g.DrawString($titleStr, $titleFont, $titleBrush, $tx, $ty)
$titleBrush.Dispose(); $shadowBrush.Dispose(); $titleFont.Dispose()

# â”€â”€ 6. Tagline
$tagFont = New-Object System.Drawing.Font "Segoe UI", 13, ([System.Drawing.FontStyle]::Italic)
$tagBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 166, 173, 200))
$tagStr   = "Loading greatness... only a moment"
$tagSize  = $g.MeasureString($tagStr, $tagFont)
$g.DrawString($tagStr, $tagFont, $tagBrush, (($W - $tagSize.Width) / 2), 285)
$tagBrush.Dispose(); $tagFont.Dispose()

# â”€â”€ 7. Loading bar (decorative â€” splash is static)
$barW = 320; $barH = 6
$bx = ($W - $barW) / 2; $by = 330
$trackBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 49, 50, 68))
$g.FillRectangle($trackBrush, $bx, $by, $barW, $barH)
$trackBrush.Dispose()
# Fill ~60% â€” gives "still loading" vibe
$fillGrad = New-Object System.Drawing.Drawing2D.LinearGradientBrush (New-Object System.Drawing.RectangleF $bx, $by, ($barW * 0.65), $barH), ([System.Drawing.Color]::FromArgb(255, 137, 180, 250)), ([System.Drawing.Color]::FromArgb(255, 203, 166, 247)), 0.0
$g.FillRectangle($fillGrad, $bx, $by, ($barW * 0.65), $barH)
$fillGrad.Dispose()

# â”€â”€ 8. Version footer
$verFont = New-Object System.Drawing.Font "Consolas", 9
$verBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(180, 108, 112, 134))
$g.DrawString("v1.0.0  |  C# WPF Edition  |  RageQyt 2026", $verFont, $verBrush, 14, ($H - 22))
$verBrush.Dispose(); $verFont.Dispose()

$g.Dispose()

$out = "C:\Users\prkid\Documents\macro_recorder\QytsCreations_CS\Resources\splash.png"
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"Splash written: $out ($(((Get-Item $out).Length / 1KB).ToString('0.0')) KB)"

