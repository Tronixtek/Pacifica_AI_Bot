import { BotPerformanceBoard } from "./bot-performance";

// The dashboard shows bot performance and nothing else. Signals, charts,
// diagnostics and operator controls were removed deliberately: with three bots
// sharing one account, the only question the screen needs to answer is which
// of them is making or losing money.
export default function Page() {
  return <BotPerformanceBoard />;
}
