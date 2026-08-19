import { StockExplorer } from "@/components/stock-explorer";
import { getDashboard } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function Home() {
  const dashboard = await getDashboard();

  return (
    <main className="min-h-screen bg-stone-950 text-stone-100">
      <StockExplorer dashboard={dashboard} />
    </main>
  );
}
