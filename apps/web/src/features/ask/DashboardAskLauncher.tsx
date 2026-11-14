import { Bot } from "lucide-react";

import { useAppName } from "../../app/branding";
import { Button } from "../../components/ui/button";

export interface AskAbout {
  id: string;
  title: string;
}

/** The Ask panel loads on first use (US-148 bundle budget); pointing at the launcher starts loading it. */
export const loadDashboardAskPanel = () => import("./DashboardAskPanel");

/** The chat launcher at the bottom right of a dashboard (US-189): opens Ask beside it. */
export function DashboardAskLauncher({ onOpen }: { onOpen: () => void }) {
  const appName = useAppName();
  return (
    <Button size="lg" className="fixed bottom-6 right-6 z-40 rounded-full shadow-overlay" onClick={onOpen}
      onPointerEnter={() => void loadDashboardAskPanel()} onFocus={() => void loadDashboardAskPanel()}
      aria-label={`Ask ${appName} about this dashboard`}>
      <Bot aria-hidden />Ask
    </Button>
  );
}
