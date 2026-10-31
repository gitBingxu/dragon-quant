import React from "react";
import { Link, navigate, usePathname, useSearchParams } from "./router";
import {
  IconCalendar,
  IconGrid,
  IconRadar,
  IconSigma,
  IconWallet,
} from "./components/icons";

/** README 品牌图标（assets/icon.svg）：红渐变圆角方块 + 白色 K 线蜡烛。 */
function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" aria-hidden="true">
      <defs>
        <linearGradient id="dq-brand" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#D93A32" />
          <stop offset="100%" stopColor="#AE201E" />
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="112" fill="url(#dq-brand)" />
      <g transform="translate(100,105)">
        <line x1="50" y1="174" x2="50" y2="336" stroke="#FFFFFF" strokeWidth="9" strokeLinecap="round" />
        <rect x="0" y="200" width="100" height="110" rx="16" fill="#FFFFFF" />
        <rect x="106" y="76" width="100" height="28" rx="14" fill="#FFFFFF" />
        <rect x="212" y="-34" width="100" height="28" rx="14" fill="#FFFFFF" />
      </g>
    </svg>
  );
}

const NAV = [
  { to: "/", label: "总览", icon: IconGrid, match: (p: string) => p === "/" },
  { to: "/scans", label: "扫描", icon: IconRadar, match: (p: string) => p.startsWith("/scans") },
  { to: "/factors", label: "因子", icon: IconSigma, match: (p: string) => p === "/factors" },
  { to: "/prep", label: "复盘", icon: IconCalendar, match: (p: string) => p === "/prep" },
  { to: "/account", label: "账户", icon: IconWallet, match: (p: string) => p === "/account" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const source = search.get("source") === "v1" ? "v1" : "v2";

  const changeSource = (next: "v1" | "v2") => {
    if (next === source) return;
    const params = new URLSearchParams(window.location.search);
    params.set("source", next);
    const query = params.toString();
    navigate(`${pathname}${query ? `?${query}` : ""}`);
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-hairline bg-[rgba(11,14,20,0.82)] backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-4 px-4 sm:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2.5 no-underline">
            <BrandMark size={28} />
            <span className="hidden flex-col leading-none sm:flex">
              <span className="text-[13px] font-semibold tracking-[0.02em] text-ink">
                DRAGON QUANT
              </span>
              <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.22em] text-ink-3">
                龙头量化终端
              </span>
            </span>
          </Link>

          <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {NAV.map((item) => {
              const active = item.match(pathname);
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-[12.5px] font-medium no-underline transition-colors ${
                    active
                      ? "bg-brand-soft text-brand-bright ring-1 ring-inset ring-brand/30"
                      : "text-ink-2 hover:bg-white/5 hover:text-ink"
                  }`}
                >
                  <Icon size={15} />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="flex shrink-0 items-center gap-2">
            <span className="hidden font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-3 md:inline">
              数据体系
            </span>
            <div className="seg" role="group" aria-label="数据体系">
              <button
                type="button"
                className="seg-item"
                data-active={source === "v2" ? "true" : "false"}
                onClick={() => changeSource("v2")}
              >
                v2 五维
              </button>
              <button
                type="button"
                className="seg-item"
                data-active={source === "v1" ? "true" : "false"}
                onClick={() => changeSource("v1")}
              >
                v1 历史
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 sm:py-6">{children}</main>

      <footer className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-2 px-4 pb-6 pt-2 text-[11px] text-ink-3 sm:px-6">
        <span>Dragon Quant · 龙头战法量化筛选系统</span>
        <span className="font-mono">A股语义：红涨 · 绿跌</span>
      </footer>
    </div>
  );
}
