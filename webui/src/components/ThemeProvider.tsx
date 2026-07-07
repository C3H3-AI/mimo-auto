import React from "react";
import {
  ThemeProvider as MuiThemeProvider,
  createTheme,
} from "@mui/material/styles";
import { useUiStore } from "../store/uiStore";
import { lightTheme, darkTheme } from "../theme";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const themeMode = useUiStore((s) => s.themeMode);

  const resolvedTheme = React.useMemo(() => {
    if (themeMode === "system") {
      const prefersDark =
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-color-scheme: dark)").matches;
      return prefersDark ? darkTheme : lightTheme;
    }
    return themeMode === "dark" ? darkTheme : lightTheme;
  }, [themeMode]);

  const theme = React.useMemo(() => createTheme(resolvedTheme), [resolvedTheme]);

  return <MuiThemeProvider theme={theme}>{children}</MuiThemeProvider>;
}
