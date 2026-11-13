import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { ArrowLeft, Clapperboard, LogOut } from "lucide-react";
import { Logo, LogoMark } from "@/components/shotvi/Logo";
import { useAppStore } from "@/store/useAppStore";
import { minutesLabel, useWorkspace } from "@/hooks/useKlipvi";

const NAV = [{ to: "/dashboard", label: "Episodes", icon: Clapperboard }];

function MinutesBox({ workspace }) {
  if (!workspace) return null;
  if (workspace.plan === "internal") {
    return <p className="text-sm text-k-muted">Team workspace. Minutes are not counted.</p>;
  }
  return (
    <div>
      <p className="text-k-text">
        <span className="text-lg font-semibold">{minutesLabel(workspace.minutes_free)}</span>{" "}
        <span className="text-sm text-k-muted">free</span>
      </p>
      {workspace.minutes_held > 0 && (
        <p className="text-sm text-k-muted">{minutesLabel(workspace.minutes_held)} in progress</p>
      )}
    </div>
  );
}

/** The signed-in frame: menu on the left (top on phones), the workspace and its minutes always in view. */
export default function Shell({ title, subtitle, back, actions, children, compact = false }) {
  const navigate = useNavigate();
  const signOut = useAppStore((s) => s.signOut);
  const { data: me, workspaces, workspace, choose } = useWorkspace();

  const onSignOut = async () => {
    await signOut?.();
    navigate("/auth");
  };

  if (compact) {
    // the editor: a slim icon rail so the work area gets the screen
    return (
      <div className="min-h-screen bg-k-bg text-k-text md:flex">
        <aside className="hidden md:flex md:w-16 md:shrink-0 md:h-screen md:sticky md:top-0 md:flex-col md:items-center gap-2 py-4 border-r border-k-line bg-k-surface/40">
          <Link to="/dashboard" aria-label="Klipvi: your episodes" title="Klipvi" className="mb-3">
            <LogoMark size={30} />
          </Link>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={label}
              aria-label={label}
              className={({ isActive }) => `rounded-lg p-2.5 ${isActive ? "bg-k-raised text-k-text" : "text-k-muted hover:text-k-text hover:bg-k-raised/60"}`}
            >
              <Icon size={19} aria-hidden="true" />
            </NavLink>
          ))}
          <button onClick={onSignOut} className="mt-auto rounded-lg p-2.5 text-k-subtle hover:text-k-text hover:bg-k-raised/60" aria-label="Sign out" title="Sign out">
            <LogOut size={17} />
          </button>
        </aside>
        <main className="flex-1 min-w-0">
          <header className="px-4 md:px-6 pt-4 pb-1 flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 flex items-center gap-3">
              {back && (
                <Link
                  to={back.to}
                  className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-k-line bg-k-surface px-3 py-2 text-sm font-semibold text-k-soft hover:text-k-text hover:border-k-line2"
                >
                  <ArrowLeft size={16} aria-hidden="true" />
                  {back.label}
                </Link>
              )}
              {title && <h1 className="font-display text-xl md:text-[1.4rem] font-extrabold leading-tight truncate">{title}</h1>}
            </div>
            {actions}
          </header>
          <div className="px-4 md:px-6 pb-4">{children}</div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-k-bg text-k-text md:flex">
      <aside className="md:w-60 md:shrink-0 md:h-screen md:sticky md:top-0 border-b md:border-b-0 md:border-r border-k-line bg-k-surface/40 flex md:flex-col">
        <div className="flex items-center justify-between w-full px-4 py-4 md:py-5 md:px-5">
          <Logo />
          {/* phones: minutes and sign out stay reachable in the top bar */}
          <div className="md:hidden flex items-center gap-3 text-sm">
            {workspace && workspace.plan !== "internal" && (
              <span className="text-k-muted">
                <span className="font-semibold text-k-text">{minutesLabel(workspace.minutes_free)}</span> free
              </span>
            )}
            <button onClick={onSignOut} className="rounded-md p-1.5 text-k-subtle hover:text-k-text" aria-label="Sign out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
        <nav className="hidden md:block px-3 space-y-1" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-[15px] ${
                  isActive ? "bg-k-raised text-k-text" : "text-k-muted hover:text-k-text hover:bg-k-raised/60"
                }`
              }
            >
              <Icon size={18} aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="hidden md:flex md:flex-col gap-4 mt-auto px-5 py-5 border-t border-k-line">
          {workspaces.length > 1 ? (
            <label className="block">
              <span className="sr-only">Workspace</span>
              <select
                value={workspace?.id || ""}
                onChange={(e) => choose(e.target.value)}
                className="w-full rounded-lg bg-k-raised border border-k-line px-2.5 py-2 text-sm text-k-text"
              >
                {workspaces.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="font-semibold text-k-text truncate">{workspace?.name || " "}</p>
          )}
          <MinutesBox workspace={workspace} />
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="text-sm text-k-subtle truncate" title={me?.user?.email}>
              {me?.user?.email}
            </span>
            <button
              onClick={onSignOut}
              className="shrink-0 rounded-md p-1.5 text-k-subtle hover:text-k-text hover:bg-k-raised"
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 min-w-0">
        <header className={`px-4 md:px-10 ${compact ? "pt-4 md:pt-5" : "pt-6 md:pt-10"} pb-2 flex flex-wrap items-end justify-between gap-4 ${compact ? "" : "max-w-[1280px]"}`}>
          <div className="min-w-0">
            {back && (
              <Link to={back.to} className="text-sm text-k-muted hover:text-k-text">
                {back.label}
              </Link>
            )}
            {title && (
              <h1 className={`font-display font-extrabold leading-tight mt-1 ${compact ? "text-xl md:text-2xl" : "text-3xl md:text-[2.4rem]"}`}>{title}</h1>
            )}
            {subtitle && <p className="mt-1 text-k-subtle">{subtitle}</p>}
          </div>
          {actions}
        </header>
        <div className={`px-4 md:px-10 ${compact ? "pb-4 max-w-[1600px]" : "pb-16 max-w-[1280px]"}`}>{children}</div>
      </main>
    </div>
  );
}
