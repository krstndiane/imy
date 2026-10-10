import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { api } from "./api";
import "./styles.css";

createRoot(document.getElementById("root")).render(<App />);

// Count this visit for the admin page (once per page load; it never stops the app if it fails).
api.visit("app").catch(() => {});
