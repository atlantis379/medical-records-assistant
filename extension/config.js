// Where the local service is. Served by the service itself (http://127.0.0.1:8765/app/) the page talks to the
// address it was loaded from; opened as a browser extension page it uses the fixed local address.
const SERVICE_BASE = /^https?:$/.test(location.protocol) ? location.origin : "http://127.0.0.1:8765";
const SERVICE_WS = SERVICE_BASE.replace(/^http/, "ws") + "/ws/transcribe";
