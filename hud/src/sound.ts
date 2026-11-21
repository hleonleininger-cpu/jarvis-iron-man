// Hinweistöne (M3.3), erzeugt mit der Web-Audio-API: keine Tondateien, keine Lizenzfragen.
// Ohne Audio-Unterstützung oder vor der ersten Bedienung (Autoplay-Sperre) bleibt es still.

// Kein Ton "Zuhören": den spielt der Sprachclient selbst, und ein zweiter aus dem HUD landete
// über Lautsprecher im Mikrofon.
export type SoundName = "boot" | "confirm" | "stop" | "error";

// Tonfolgen: [Frequenz in Hz, Beginn in s, Dauer in s]
const PATTERNS: Record<SoundName, [number, number, number][]> = {
  boot: [
    [392, 0, 0.12],
    [523, 0.12, 0.12],
    [784, 0.24, 0.25],
  ],
  confirm: [
    [880, 0, 0.12],
    [880, 0.2, 0.12],
  ],
  stop: [
    [330, 0, 0.18],
    [220, 0.18, 0.3],
  ],
  error: [[200, 0, 0.3]],
};

type AudioContextLike = Pick<AudioContext, "currentTime" | "destination" | "state" | "resume" | "createOscillator" | "createGain">;

export function createSounds(makeContext: () => AudioContextLike | null = defaultContext) {
  let context: AudioContextLike | null = null;
  return {
    play(name: SoundName, volume: number): void {
      if (volume <= 0) return;
      context ??= makeContext();
      if (!context) return;
      if (context.state !== "running") {
        // Autoplay-Sperre (noch keine Bedienung): Ton verwerfen statt aufstauen, sonst kämen
        // später alle zugleich.
        void context.resume().catch(() => {});
        return;
      }
      const start = context.currentTime;
      for (const [frequency, offset, duration] of PATTERNS[name]) {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        const begin = start + offset;
        gain.gain.setValueAtTime(0, begin);
        gain.gain.linearRampToValueAtTime(0.25 * volume, begin + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, begin + duration);
        oscillator.connect(gain).connect(context.destination);
        oscillator.start(begin);
        oscillator.stop(begin + duration + 0.02);
      }
    },
  };
}

function defaultContext(): AudioContextLike | null {
  const Ctor = (globalThis as { AudioContext?: typeof AudioContext }).AudioContext;
  try {
    return Ctor ? new Ctor() : null;
  } catch {
    return null;
  }
}
