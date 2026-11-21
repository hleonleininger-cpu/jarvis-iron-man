import type { Settings } from "../settings";

/** Kleines Einstellungsmenü: Startanimation, Töne, Lautstärke. */
export function SettingsMenu({ settings, onChange }: { settings: Settings; onChange: (next: Settings) => void }) {
  return (
    <details className="settings">
      <summary>Einstellungen</summary>
      <label>
        <input type="checkbox" checked={settings.boot} onChange={(e) => onChange({ ...settings, boot: e.target.checked })} />
        Startanimation
      </label>
      <label>
        <input type="checkbox" checked={settings.sound} onChange={(e) => onChange({ ...settings, sound: e.target.checked })} />
        Töne
      </label>
      <label>
        Lautstärke
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={settings.volume}
          disabled={!settings.sound}
          onChange={(e) => onChange({ ...settings, volume: Number(e.target.value) })}
        />
      </label>
    </details>
  );
}
