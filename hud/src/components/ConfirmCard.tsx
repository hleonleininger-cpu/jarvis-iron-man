import type { ConfirmItem } from "../state";

interface Props {
  item: ConfirmItem;
  onAnswer: (id: string, approve: boolean) => void;
}

/** Rückfrage vor einer schreibenden Aktion (ADR-005). Text immer als Text, nie als HTML. */
export function ConfirmCard({ item, onAnswer }: Props) {
  return (
    <article className="confirm" aria-label={`Rückfrage zu ${item.tool}`}>
      <header>
        Rückfrage · <span className="act-tool">{item.tool}</span>{" "}
        <span className={`risk risk-${item.risk}`}>{item.risk}</span>
      </header>
      <pre className="preview">{item.preview}</pre>
      <p>{item.question}</p>
      <div className="actions">
        <button type="button" className="yes" onClick={() => onAnswer(item.id, true)}>
          Ja
        </button>
        <button type="button" className="no" onClick={() => onAnswer(item.id, false)}>
          Nein
        </button>
      </div>
    </article>
  );
}
