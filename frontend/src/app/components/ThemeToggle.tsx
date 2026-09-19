"use client";

import { useEffect, useSyncExternalStore } from "react";

import styles from "./ThemeToggle.module.css";

type Theme = "light" | "dark";

const THEME_COOKIE_KEY = "nl-nfc-theme";

const themeListeners = new Set<() => void>();

function getThemeSnapshot(): Theme {
  if (typeof document === "undefined") {
    return "light";
  }

  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function getStoredTheme(): Theme {
  if (typeof document === "undefined" || typeof document.cookie !== "string") {
    return "light";
  }

  return document.cookie
    .split("; ")
    .some((cookie) => cookie === `${THEME_COOKIE_KEY}=dark`)
    ? "dark"
    : "light";
}

function subscribeToTheme(listener: () => void) {
  themeListeners.add(listener);

  return () => themeListeners.delete(listener);
}

function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.cookie = `${THEME_COOKIE_KEY}=${theme}; Path=/; Max-Age=31536000; SameSite=Lax`;
  themeListeners.forEach((listener) => listener());
}

type ThemeToggleProps = {
  variant?: "default" | "onIntro";
};

export default function ThemeToggle({ variant = "default" }: ThemeToggleProps) {
  const theme = useSyncExternalStore(
    subscribeToTheme,
    getThemeSnapshot,
    () => "light",
  );

  useEffect(() => {
    const storedTheme = getStoredTheme();

    if (storedTheme !== getThemeSnapshot()) {
      setTheme(storedTheme);
    }
  }, []);

  function alternarTema() {
    const nextTheme: Theme = theme === "dark" ? "light" : "dark";

    setTheme(nextTheme);
  }

  const modoEscuro = theme === "dark";

  return (
    <button
      className={`${styles.toggle} ${variant === "onIntro" ? styles.onIntro : ""}`}
      type="button"
      aria-label={modoEscuro ? "Ativar modo claro" : "Ativar modo escuro"}
      aria-pressed={modoEscuro}
      title={modoEscuro ? "Ativar modo claro" : "Ativar modo escuro"}
      onClick={alternarTema}
    >
      <svg
        className={styles.icon}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {modoEscuro ? (
          <path d="M20.5 15.2A8.5 8.5 0 0 1 8.8 3.5 8.5 8.5 0 1 0 20.5 15.2Z" />
        ) : (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
          </>
        )}
      </svg>
      <span>{modoEscuro ? "Claro" : "Escuro"}</span>
    </button>
  );
}
