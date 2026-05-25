using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using QytCroRec.Models;

namespace QytCroRec.Services;

/// <summary>Samples a screen region inside the target window, looks for red-flag text/colors,
/// fires correction key or guard macro when triggered.</summary>
public class PixelGuardService : IDisposable
{
    public event Action<string>? Triggered;   // (flagText)
    public event Action<string>? StatusEvent;

    private System.Threading.Timer? _timer;
    private readonly object _lock = new();
    private Macro? _lane;
    private IntPtr _hwnd;
    private Action<string>? _onCorrection;   // (key) handler from owner
    private Action<Macro>? _onMacroCorrection;
    private List<Macro> _guardMacros = new();

    public bool IsRunning => _timer != null;

    public void Start(Macro lane, IntPtr targetHwnd,
                      Action<string> onCorrectionKey,
                      Action<Macro> onCorrectionMacro,
                      List<Macro> guardMacros,
                      int intervalMs = 250)
    {
        Stop();
        if (!lane.PixelGuardEnabled) return;

        _lane              = lane;
        _hwnd              = targetHwnd;
        _onCorrection      = onCorrectionKey;
        _onMacroCorrection = onCorrectionMacro;
        _guardMacros       = guardMacros ?? new();

        _timer = new System.Threading.Timer(_ => Tick(), null, intervalMs, intervalMs);
        StatusEvent?.Invoke("PixelGuard started");
    }

    public void Stop()
    {
        lock (_lock)
        {
            _timer?.Dispose();
            _timer = null;
        }
    }

    private void Tick()
    {
        if (_lane == null || _hwnd == IntPtr.Zero) return;
        try
        {
            using var bmp = CaptureRegion();
            if (bmp == null) return;
            string? flag = DetectRedFlag(bmp);
            if (flag != null)
            {
                StatusEvent?.Invoke($"PixelGuard FLAG: {flag}");
                Triggered?.Invoke(flag);
                ApplyCorrection();
            }
        }
        catch (Exception ex)
        {
            StatusEvent?.Invoke($"PixelGuard error: {ex.Message}");
        }
    }

    private Bitmap? CaptureRegion()
    {
        if (_lane == null) return null;
        if (!Win32.GetClientRect(_hwnd, out var cr)) return null;
        var topLeft = new Win32.POINT { X = 0, Y = 0 };
        Win32.ClientToScreen(_hwnd, ref topLeft);

        int x = topLeft.X + (int)(cr.Right  * _lane.PixelGuardCapXPct);
        int y = topLeft.Y + (int)(cr.Bottom * _lane.PixelGuardCapYPct);
        int w = Math.Max(8, (int)(cr.Right  * _lane.PixelGuardCapWPct));
        int h = Math.Max(8, (int)(cr.Bottom * _lane.PixelGuardCapHPct));

        var bmp = new Bitmap(w, h, PixelFormat.Format32bppArgb);
        using var g = Graphics.FromImage(bmp);
        g.CopyFromScreen(x, y, 0, 0, new Size(w, h), CopyPixelOperation.SourceCopy);
        return bmp;
    }

    /// <summary>Cheap detector: if more than threshold% of pixels are "red-dominant", fire.
    /// Future: integrate OCR for actual text matching against PixelGuardRedFlags.</summary>
    private string? DetectRedFlag(Bitmap bmp)
    {
        if (_lane == null) return null;
        int redCount = 0, total = 0;
        var data = bmp.LockBits(new Rectangle(0, 0, bmp.Width, bmp.Height),
                                ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            int bytes = Math.Abs(data.Stride) * bmp.Height;
            byte[] buf = new byte[bytes];
            Marshal.Copy(data.Scan0, buf, 0, bytes);
            for (int i = 0; i + 3 < bytes; i += 4)
            {
                byte b = buf[i], g = buf[i + 1], r = buf[i + 2];
                if (r > 150 && r > g + 40 && r > b + 40) redCount++;
                total++;
            }
        }
        finally { bmp.UnlockBits(data); }

        double pct = total > 0 ? (double)redCount / total : 0;
        if (pct > 0.05)  // 5% red pixels triggers
            return _lane.PixelGuardRedFlags.FirstOrDefault() ?? "red-detected";
        return null;
    }

    private void ApplyCorrection()
    {
        if (_lane == null) return;
        if (!string.IsNullOrEmpty(_lane.PixelGuardCorrectionMacro))
        {
            var m = _guardMacros.FirstOrDefault(g => g.Id == _lane.PixelGuardCorrectionMacro
                                                  || g.Name == _lane.PixelGuardCorrectionMacro);
            if (m != null) _onMacroCorrection?.Invoke(m);
        }
        else if (!string.IsNullOrEmpty(_lane.PixelGuardCorrectionKey))
        {
            _onCorrection?.Invoke(_lane.PixelGuardCorrectionKey);
        }
    }

    public void Dispose() => Stop();
}
