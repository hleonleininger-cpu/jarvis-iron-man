interface Props {
  onStop: () => void;
  failed: boolean;
}

/** Not-Aus (ADR-018): in jedem Zustand sichtbar. Antwortet der Core nicht, hilft nur das Terminal. */
export function StopButton({ onStop, failed }: Props) {
  return (
    <div className="stop">
      <button type="button" className="stop-button" onClick={onStop} title="Not-Aus (Strg+.)">
        NOT-AUS
      </button>
      {failed ? (
        <p className="stop-failed" role="alert">
          Der Core antwortet nicht. Im Terminal hilft: <code>jarvis stop</code>
        </p>
      ) : null}
    </div>
  );
}
