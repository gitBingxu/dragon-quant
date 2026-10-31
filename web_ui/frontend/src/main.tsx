import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import { AppShell } from "./AppShell";
import { Router } from "./router";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppShell>
      <Router />
    </AppShell>
  </React.StrictMode>
);
