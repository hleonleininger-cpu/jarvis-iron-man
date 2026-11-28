import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Wird gerufen, wenn der Inhalt scheitert (z. B. 3D-Baustein lädt nicht). */
  onError: () => void;
}

/** Fängt Fehler eines entbehrlichen Teils ab, damit der Rest des HUD (vor allem der Not-Aus)
 *  weiterläuft. Ohne sie hängte React bei einem Fehler den ganzen Baum aus. */
export class SafeBoundary extends Component<Props, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(): void {
    this.props.onError();
  }

  override render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}
