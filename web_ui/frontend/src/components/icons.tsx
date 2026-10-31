import type { ReactNode } from "react";

export interface IconProps {
  size?: number;
  strokeWidth?: number;
  className?: string;
}

export function Svg({
  size = 16,
  strokeWidth = 1.75,
  className,
  children,
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function IconDragon(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 18c2.4 0 4-1.6 4-3.9 0-1.7-1.2-2.7-2.4-3.6C4 9.4 3.3 8.2 3.3 6.8 3.3 4.9 4.8 3.5 6.7 3.5c1.9 0 3.3 1.4 3.3 3.3 0 1.3-.6 2.1-1.4 2.9" />
      <path d="M8.6 9.7c2.2.4 3.7 2.1 3.7 4.3 0 2.6-1.7 4-4.3 4H4" />
      <path d="M15.5 6.2c1.9 0 3.4 1.4 3.4 3.3 0 3.4-2.7 5.1-5.5 5.1" />
      <path d="M15.2 15.3c.6 1.2 1.8 2 3.2 2" />
      <circle cx="7" cy="6.4" r="0.55" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function IconGrid(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </Svg>
  );
}

export function IconRadar(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 12 18 7" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="16.4" cy="9.2" r="1.2" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function IconSigma(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M18 4H6l5.4 7.4L6 20h12" />
    </Svg>
  );
}

export function IconCalendar(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" />
      <path d="M3.5 9.5h17M8 3v4M16 3v4" />
    </Svg>
  );
}

export function IconWallet(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5v-9Z" />
      <path d="M4 9h16" />
      <circle cx="16.5" cy="14" r="1" fill="currentColor" stroke="none" />
    </Svg>
  );
}

export function IconSearch(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </Svg>
  );
}

export function IconRefresh(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M20 11a8 8 0 1 0-.7 4.4" />
      <path d="M20 4v7h-7" />
    </Svg>
  );
}

export function IconChevronDown(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="m6 9 6 6 6-6" />
    </Svg>
  );
}

export function IconChevronUp(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="m6 15 6-6 6 6" />
    </Svg>
  );
}

export function IconChevronLeft(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="m14 6-6 6 6 6" />
    </Svg>
  );
}

export function IconChevronRight(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="m10 6 6 6-6 6" />
    </Svg>
  );
}

export function IconTrash(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 7h16M9 7V4.5h6V7M6.5 7l.9 12.5h9.2L17.5 7" />
      <path d="M10 11v5M14 11v5" />
    </Svg>
  );
}

export function IconPlus(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function IconX(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="m6 6 12 12M18 6 6 18" />
    </Svg>
  );
}

export function IconInfo(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 10.5V16M12 7.8v.2" />
    </Svg>
  );
}
