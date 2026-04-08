export interface BrowserWalletAdapter {
  publicKey?: {
    toString(): string;
  } | null;
  isPhantom?: boolean;
  isSolflare?: boolean;
  isBackpack?: boolean;
  connect(options?: { onlyIfTrusted?: boolean }): Promise<{ publicKey?: { toString(): string } } | void>;
  disconnect?(): Promise<void>;
}

export interface DetectedWallet {
  id: string;
  label: string;
  adapter: BrowserWalletAdapter;
}

declare global {
  interface Window {
    backpack?: BrowserWalletAdapter;
    phantom?: {
      solana?: BrowserWalletAdapter;
    };
    solana?: BrowserWalletAdapter;
    solflare?: BrowserWalletAdapter;
  }
}

export function detectBrowserWallets(): DetectedWallet[] {
  if (typeof window === "undefined") {
    return [];
  }

  const discovered = new Map<string, DetectedWallet>();

  const register = (id: string, label: string, adapter: BrowserWalletAdapter | undefined) => {
    if (!adapter || discovered.has(id) || typeof adapter.connect !== "function") {
      return;
    }

    discovered.set(id, { id, label, adapter });
  };

  register("phantom", "Phantom", window.phantom?.solana ?? (window.solana?.isPhantom ? window.solana : undefined));
  register("solflare", "Solflare", window.solflare ?? (window.solana?.isSolflare ? window.solana : undefined));
  register("backpack", "Backpack", window.backpack ?? (window.solana?.isBackpack ? window.solana : undefined));

  return Array.from(discovered.values());
}

export function getConnectedWalletAddress(adapter: BrowserWalletAdapter | undefined): string | null {
  const publicKey = adapter?.publicKey;
  if (!publicKey) {
    return null;
  }

  try {
    const address = publicKey.toString();
    return address || null;
  } catch {
    return null;
  }
}

export function shortenWalletAddress(value: string | null | undefined): string {
  if (!value) {
    return "Not connected";
  }
  if (value.length <= 12) {
    return value;
  }
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}
