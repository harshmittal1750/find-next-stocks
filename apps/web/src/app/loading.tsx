export default function Loading() {
  return (
    <main className="min-h-screen bg-stone-950 px-4 text-stone-100 sm:px-6 lg:px-10">
      <div className="mx-auto max-w-[1540px] animate-pulse">
        <div className="h-16 border-b border-stone-800" />
        <div className="grid gap-8 border-b border-stone-800 py-14 lg:grid-cols-2">
          <div>
            <div className="h-3 w-40 bg-stone-800" />
            <div className="mt-6 h-12 max-w-3xl bg-stone-900" />
            <div className="mt-3 h-12 max-w-xl bg-stone-900" />
            <div className="mt-6 h-4 max-w-2xl bg-stone-900" />
          </div>
        </div>
        <div className="grid grid-cols-2 border-b border-stone-800 lg:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div className="h-32 border-r border-stone-800 p-6" key={item}>
              <div className="h-3 w-24 bg-stone-900" />
              <div className="mt-4 h-8 w-20 bg-stone-900" />
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
