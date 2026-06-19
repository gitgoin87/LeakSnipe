import React from "react";
import ReactDOM from "react-dom/client";
import { LiveHudOverlay } from "./components/LiveHudOverlay";
import "./App.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <LiveHudOverlay />
  </React.StrictMode>,
);
