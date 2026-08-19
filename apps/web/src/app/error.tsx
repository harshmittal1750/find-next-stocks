"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-950 px-6 text-stone-100">
      <div className="max-w-md border-y border-stone-800 py-10 text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--accent)]">
          Dashboard unavailable
        </p>
        <h1 className="mt-4 text-2xl font-medium tracking-[-0.03em]">The data view could not be loaded.</h1>
        <p className="mt-3 text-sm leading-6 text-stone-500">
          Check that the API is running or try the local JSON snapshot again.
        </p>
        <button
          className="mt-6 min-h-10 rounded-md bg-[var(--accent)] px-4 text-xs font-medium text-stone-950 transition-opacity hover:opacity-90 active:translate-y-px"
          onClick={reset}
          type="button"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
