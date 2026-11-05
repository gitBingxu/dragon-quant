import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import { AppShell } from "./AppShell";
import { Router } from "./router";
import { ThemeProvider } from "./theme/ThemeProvider";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AppShell>
        <Router />
      </AppShell>
    </ThemeProvider>
  </React.StrictMode>
);
