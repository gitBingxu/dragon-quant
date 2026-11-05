import type { ReactNode } from "react";

export function Section({
  title,
  description,
  right,
  children,
  className = "",
  bodyClassName = "panel-body",
}: {
  title?: ReactNode;
  description?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const hasHead = Boolean(title || description || right);
  return (
    <section className={`panel ${className}`}>
      {hasHead ? (
        <header className="panel-head">
          <div className="min-w-0">
            {title ? <h2 className="panel-title">{title}</h2> : null}
            {description ? <p className="panel-desc">{description}</p> : null}
          </div>
          {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
        </header>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}
