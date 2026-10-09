import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { loadConfig } from "./config";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

loadConfig()
  .then((config) =>
    root.render(
      <StrictMode>
        <BrowserRouter>
          <App config={config} />
        </BrowserRouter>
      </StrictMode>,
    ),
  )
  .catch((err: Error) => root.render(<p className="page error">Couldn't start: {err.message}</p>));
