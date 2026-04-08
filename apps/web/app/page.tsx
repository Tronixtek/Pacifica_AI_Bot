import { MarketingLanding } from "./marketing-landing";
import { getDashboardData } from "../lib/api";

async function DashboardPage() {
  const { snapshot, diagnostics, usingFallback } = await getDashboardData();

  return (
    <MarketingLanding
      snapshot={snapshot}
      diagnostics={diagnostics}
      usingFallback={usingFallback}
    />
  );
}

export default DashboardPage;
