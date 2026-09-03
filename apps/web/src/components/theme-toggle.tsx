"use client";

import { useEffect } from "react";

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "fns-theme";

/**
 * Runs before first paint, inlined into <head>.
 *
 * Reading the stored choice in an effect would be too late: the page would paint in the
 * server-rendered theme and then snap to the user's. Kept in sync with the component
 * below.
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var s=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
var t=s==='light'||s==='dark'?s:(window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');
document.documentElement.setAttribute('data-theme',t);
}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  // Flagged for one frame so the CSS can suppress transitions mid-swap.
  root.setAttribute("data-theme-switching", "");
  root.setAttribute("data-theme", theme);
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => root.removeAttribute("data-theme-switching"));
  });
}

/**
 * Theme switch with no theme-dependent markup.
 *
 * An earlier version chose the icon and the aria-label from React state seeded in an
 * effect. The server rendered one thing, the client another, and the resulting hydration
 * mismatch tore down hydration for the whole page — every client component on the route
 * silently stopped responding, not just this button.
 *
 * So nothing here varies between server and client: both icons are always in the DOM and
 * CSS shows whichever matches `data-theme`, which the init script has already set. The
 * button reads its current state from the DOM at click time rather than from React.
 */
export function ThemeToggle() {
  useEffect(() => {
    // Follow the OS only while the user has expressed no preference of their own.
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (event: MediaQueryListEvent) => {
      if (localStorage.getItem(THEME_STORAGE_KEY)) return;
      applyTheme(event.matches ? "light" : "dark");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const toggle = () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next: Theme = current === "light" ? "dark" : "light";
    applyTheme(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private browsing can refuse writes; the theme still applies for this session.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle light and dark theme"
      title="Toggle light and dark theme"
      className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-stone-800 text-stone-400 transition-colors hover:border-stone-600 hover:text-stone-100"
    >
      <span aria-hidden="true" className="theme-icon theme-icon--sun">
        <SunIcon />
      </span>
      <span aria-hidden="true" className="theme-icon theme-icon--moon">
        <MoonIcon />
      </span>
    </button>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
      strokeLinecap="round" className="h-4 w-4">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
      strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  );
}
