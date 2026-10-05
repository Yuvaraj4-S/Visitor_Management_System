import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

// Built into the Frappe app's public folder and served from
// /assets/visitormanagement/gate/ — www/_gate.html loads gate.js / gate.css.
export default defineConfig({
	plugins: [vue()],
	base: "/assets/visitormanagement/gate/",
	resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
	build: {
		outDir: "../visitormanagement/public/gate",
		emptyOutDir: true,
		cssCodeSplit: false,
		rollupOptions: {
			input: "src/main.js",
			output: {
				entryFileNames: "gate.js",
				chunkFileNames: "chunks/[name]-[hash].js",
				assetFileNames: (info) => (info.name && info.name.endsWith(".css") ? "gate.css" : "assets/[name]-[hash][extname]"),
			},
		},
	},
	server: {
		port: 8081,
		proxy: { "^/(api|assets|files|private)": "http://127.0.0.1:8000" },
	},
});
