import React, { useEffect, useState } from "react";
import { DragonsPage } from "./pages/dragons/DragonsPage";
import { ScansPage } from "./pages/scans/ScansPage";
import { ScanDetailPage } from "./pages/scans/ScanDetailPage";
import { FactorsPage } from "./pages/factors/FactorsPage";
import { PrepPage } from "./pages/prep/PrepPage";
import { AccountPage } from "./pages/account/AccountPage";

type RouteDef = {
  match: RegExp;
  render: (match: RegExpMatchArray) => React.ReactNode;
};

const ROUTES: RouteDef[] = [
  { match: /^\/$/, render: () => <DragonsPage /> },
  { match: /^\/scans$/, render: () => <ScansPage /> },
  {
    match: /^\/scans\/([^/]+)$/,
    render: (match) => <ScanDetailPage scanId={decodeURIComponent(match[1])} />,
  },
  { match: /^\/factors$/, render: () => <FactorsPage /> },
  { match: /^\/prep$/, render: () => <PrepPage /> },
  { match: /^\/account$/, render: () => <AccountPage /> },
];

export function usePathname(): string {
  const [pathname, setPathname] = useState(() => window.location.pathname);
  useEffect(() => {
    const update = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return pathname.replace(/\/+$/, "") || "/";
}

export function useSearchParams(): URLSearchParams {
  const [search, setSearch] = useState(() => window.location.search);
  useEffect(() => {
    const update = () => setSearch(window.location.search);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return new URLSearchParams(search);
}

export function navigate(to: string, opts?: { replace?: boolean }) {
  if (opts?.replace) {
    history.replaceState(null, "", to);
  } else {
    history.pushState(null, "", to);
  }
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function Navigate({ to }: { to: string }) {
  useEffect(() => {
    navigate(to, { replace: true });
  }, [to]);
  return null;
}

export function Link({
  to,
  children,
  className,
  style,
}: {
  to: string;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <a
      href={to}
      className={className}
      style={style}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export function Router() {
  const pathname = usePathname();
  const route = ROUTES.find((item) => item.match.test(pathname));
  if (!route) return <Navigate to="/" />;
  return <>{route.render(pathname.match(route.match)!)}</>;
}
