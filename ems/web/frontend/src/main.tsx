import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { applyTheme, readStoredTheme } from "./theme";

// Apply the cached theme synchronously, before React paints, to avoid a wrong-theme flash.
applyTheme(readStoredTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
