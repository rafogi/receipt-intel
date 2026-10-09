import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Port 5173 is the origin registered with Cognito and the API's CORS for local dev.
  server: { port: 5173, strictPort: true },
  build: {
    // No inline scripts or styles, so the CloudFront CSP can stay strict.
    assetsInlineLimit: 0,
  },
});
