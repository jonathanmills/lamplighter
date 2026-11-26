// SPDX-License-Identifier: GPL-2.0-or-later
// Copyright (C) 2026 Jonathan Mills
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The back end serves dist/ (same origin as the API), so there is no
// dev server or proxy: "npm run watch" rebuilds on every change.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    chunkSizeWarningLimit: 900,
  },
});
