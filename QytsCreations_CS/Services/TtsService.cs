using System.Speech.Synthesis;

namespace QytCroRec.Services;

public static class TtsService
{
    private static readonly SpeechSynthesizer _synth = new();

    static TtsService()
    {
        _synth.Rate  = 1;
        _synth.Volume = 100;
    }

    public static void Speak(string text)
    {
        Task.Run(() =>
        {
            try { _synth.SpeakAsync(text); }
            catch { /* TTS optional */ }
        });
    }
}
