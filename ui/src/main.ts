import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "./app.css";

import { mount } from "svelte";
import App from "./App.svelte";
import { initToken } from "./lib/api";

initToken();
mount(App, { target: document.getElementById("app")! });
