import React, { useEffect, useRef } from "react";
import { Link, usePathname } from "./router";
import { ThemeToggle } from "./components/ThemeToggle";
import {
  IconCalendar,
  IconGrid,
  IconRadar,
  IconSigma,
  IconWallet,
} from "./components/icons";
import { IconClock, IconTrendUp } from "./components/icons-extra";

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

/**
 * 导航按两个核心组织（D1）：
 * 1) 龙头识别：scan → review 机会质量与候选对照；
 * 2) 账户验证：prep / auction / 买卖点都服务真实账户结果；
 * 3) 次级入口：扫描研究、扫描日志、因子。全部是真实路由，不设伪入口。
 * 保留全部旧深链（/scans、/prep、/auction、/account、/bs、/factors）。
 */
const PRIMARY_NAV = [
  { to: "/", label: "龙头识别", icon: IconGrid, match: (p: string) => p === "/" },
  { to: "/account", label: "账户验证", icon: IconWallet, match: (p: string) => p === "/account" },
];

const SECONDARY_NAV = [
  { to: "/prep", label: "复盘", icon: IconCalendar, match: (p: string) => p === "/prep" },
  { to: "/auction", label: "竞价", icon: IconClock, match: (p: string) => p === "/auction" },
  { to: "/bs", label: "买卖点", icon: IconTrendUp, match: (p: string) => p === "/bs" },
  { to: "/research", label: "扫描研究", icon: IconRadar, match: (p: string) => p === "/research" },
  { to: "/scans", label: "扫描日志", icon: IconRadar, match: (p: string) => p.startsWith("/scans") },
  { to: "/factors", label: "因子", icon: IconSigma, match: (p: string) => p === "/factors" },
];

/**
 * 顶栏在窄屏会换行（高度 > 56px），把真实高度写进 ``--dq-header-h``，
 * 供日历面板 sticky 使用，避免固定 ``top-14`` 遮挡内容。
 */
function useHeaderHeightVar(ref: React.RefObject<HTMLElement>) {
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const apply = () => {
      const height = Math.ceil(node.getBoundingClientRect().height);
      document.documentElement.style.setProperty(
        "--dq-header-h",
        `${height > 0 ? height : 56}px`
      );
    };
    apply();
    window.addEventListener("resize", apply);
    const observer =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    observer?.observe(node);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", apply);
    };
  }, [ref]);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const headerRef = useRef<HTMLElement>(null);
  useHeaderHeightVar(headerRef);

  return (
    <div className="min-h-screen">
      <header
        ref={headerRef}
        className="sticky top-0 z-40 border-b border-hairline bg-header backdrop-blur-xl"
      >
        <div className="mx-auto flex min-h-14 max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 sm:px-6 2xl:max-w-none 2xl:px-16">
          <Link to="/" className="order-1 flex shrink-0 items-center gap-2.5 no-underline">
            <BrandMark size={28} />
            <span className="hidden flex-col leading-none sm:flex">
              <span className="text-body font-semibold tracking-[0.02em] text-ink">
                DRAGON QUANT
              </span>
              <span className="mt-1 font-mono text-caption uppercase tracking-[0.22em] text-ink-3">
                龙头量化终端
              </span>
            </span>
          </Link>

          <nav className="order-3 flex w-full min-w-0 flex-wrap items-center gap-1 lg:order-2 lg:w-auto lg:flex-1 lg:flex-nowrap lg:overflow-x-auto">
            {PRIMARY_NAV.map((item) => {
              const active = item.match(pathname);
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-body font-semibold no-underline transition-colors ${
                    active
                      ? "bg-brand-soft text-brand-bright ring-1 ring-inset ring-brand-focus"
                      : "text-ink hover:bg-hover"
                  }`}
                >
                  <Icon size={15} />
                  {item.label}
                </Link>
              );
            })}
            <span
              aria-hidden="true"
              className="mx-1 h-5 w-px shrink-0 bg-hairline"
            />
            {SECONDARY_NAV.map((item) => {
              const active = item.match(pathname);
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-1.5 text-caption font-medium no-underline transition-colors ${
                    active
                      ? "bg-brand-soft text-brand-bright ring-1 ring-inset ring-brand-focus"
                      : "text-ink-2 hover:bg-hover hover:text-ink"
                  }`}
                >
                  <Icon size={14} />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="order-2 ml-auto flex shrink-0 items-center gap-2 lg:order-3 lg:ml-0">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 sm:py-6 2xl:max-w-none 2xl:px-16">{children}</main>

      <footer className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-2 px-4 pb-6 pt-2 text-caption text-ink-3 sm:px-6 2xl:max-w-none 2xl:px-16">
        <span>Dragon Quant · 龙头战法量化筛选系统</span>
        <span className="font-mono">A股语义：红涨 · 绿跌</span>
      </footer>
    </div>
  );
}
