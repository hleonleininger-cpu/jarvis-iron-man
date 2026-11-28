import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createTauriTransport, isDesktop } from "./api/tauri";
import { createFetchTransport } from "./api/transport";
import { App } from "./App";
import "./theme/hud.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App transport={isDesktop() ? createTauriTransport() : createFetchTransport()} />
    </StrictMode>,
  );
}
