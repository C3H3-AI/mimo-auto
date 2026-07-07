import { CssBaseline, Snackbar, Alert } from "@mui/material"
import { ThemeProvider } from "./components/ThemeProvider"
import { Layout } from "./components/Layout"
import { useSessionStore } from "./store/sessionStore"

// App boot logging for debugging
console.log("[MiMo SPA] App started", new Date().toISOString());

function ToastBar() {
  const toasts = useSessionStore((s) => s.toasts);
  const dismissToast = useSessionStore((s) => s.dismissToast);
  const last = toasts[toasts.length - 1];

  if (!last) return null;
  return (
    <Snackbar open autoHideDuration={4000} onClose={() => dismissToast(last.id)} anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
      <Alert onClose={() => dismissToast(last.id)} severity={last.severity} variant="filled" sx={{ width: "100%" }}>
        {last.message}
      </Alert>
    </Snackbar>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <CssBaseline />
      <Layout />
      <ToastBar />
    </ThemeProvider>
  )
}