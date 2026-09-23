import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
import { Modal } from "../components/Modal";
import { useRoute } from "./router";
import { Inbox, Drafts } from "../features/Queues";
import { Evidence } from "../features/Evidence";
import { Transfer } from "../features/Transfer";
import { DraftFlow } from "../features/Delivery";
const nav = [
  ["inbox", "Inbox"],
  ["drafts", "Drafts"],
  ["evidence", "Evidence"],
  ["transfer", "Transfer"],
] as const;
function Shell({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
}) {
  const route = useRoute();
  const [draft, setDraft] = useState<RecordItem | null | undefined>(undefined);
  const [refresh, setRefresh] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [connection, setConnection] = useState<"connecting" | "connected" | "offline" | "expired">("connecting");
  const authProbe = useRef(0);
  const [reauthPrompt, setReauthPrompt] = useState(false);
  const [lastSync, setLastSync] = useState("");
  const [pending, setPending] = useState<{ label: string; run: () => void } | null>(null);
  useEffect(() => {
    const events = new EventSource("/api/events");
    events.onopen = () => { authProbe.current++; setConnection("connected"); setLastSync(new Date().toISOString()); };
    events.onerror = () => {
      setConnection("offline");
      const probe = ++authProbe.current;
      void api.get<{ active: boolean }>("/api/session-status").then(current => {
        if (authProbe.current === probe && current.active === false) {
          setConnection("expired");
          events.close();
        }
      }).catch(() => undefined);
    };
    const f = () => { setConnection("connected"); setLastSync(new Date().toISOString()); setRefresh((v) => v + 1); };
    events.addEventListener("change", f);
    events.addEventListener("reset", f);
    return () => { authProbe.current++; events.close(); };
  }, []);
  useEffect(() => {
    const hashRoute = location.hash.startsWith("#/") ? location.hash.slice(2) || "inbox" : "inbox";
    if (draft !== undefined && route !== "drafts" && hashRoute !== "drafts") {
      const target = route;
      if (dirty) {
        setPending({ label: `opening ${target}`, run: () => { setDraft(undefined); location.hash = `#/${target}`; } });
        location.hash = "#/drafts";
      } else setDraft(undefined);
    }
  }, [route, draft, dirty]);
  useEffect(() => {
    document.documentElement.scrollTop = 0;
    document.documentElement.scrollLeft = 0;
    document.body.scrollTop = 0;
    document.body.scrollLeft = 0;
  }, [route, draft]);
  async function logoutNow() {
    await api.post("/api/logout");
    api.csrf = null;
    onLogout();
  }
  function reauthenticate() {
    api.csrf = null;
    onLogout();
  }
  function guarded(label: string, run: () => void) {
    if (dirty) setPending({ label, run });
    else run();
  }
  function navigate(target: string) {
    guarded(`opening ${target}`, () => { setDraft(undefined); location.hash = `#/${target}`; });
  }
  let content: React.ReactNode;
  if (draft !== undefined && (route === "drafts" || dirty))
    content = (
      <DraftFlow
        selected={draft}
        online={connection === "connected"}
        role={session.user?.role ?? ""}
        refreshKey={refresh}
        onDirtyChange={setDirty}
        onBack={() => navigate("drafts")}
      />
    );
  else if (route === "inbox")
    content = (
      <Inbox
        online={connection === "connected"}
        refreshKey={refresh}
        onOpen={(item) => {
          setDraft(item);
          location.hash = "#/drafts";
        }}
      />
    );
  else if (route === "evidence") content = <Evidence refreshKey={refresh} online={connection === "connected"} />;
  else if (route === "transfer") content = <Transfer online={connection === "connected"} role={session.user?.role ?? ""} />;
  else content = <Drafts refreshKey={refresh} onOpen={(item) => setDraft(item)} />;
  return (
    <div className="app">
      <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>Skip to content</a>
      <header className="topbar">
        <a className="brand" href="#/inbox" onClick={(event) => { event.preventDefault(); navigate("inbox"); }}>
          <span className="brand-seal">M</span>
          <span>Merlin</span>
        </a>
        <span className="desk-note">writing desk / private LAN</span>
        <div className="top-actions">
          <span><i className={`status-dot ${connection}`} />{connection}{lastSync ? ` · ${new Date(lastSync).toLocaleTimeString()}` : ""}</span>
          <span>{session.user?.name}</span>
          <button disabled={connection !== "connected"} onClick={() => guarded("signing out", () => void logoutNow())}>Sign out</button>
        </div>
      </header>
      <div className="body">
        <nav className="sidebar" aria-label="Primary navigation">
          <p className="sidebar-title">{session.engagement.name}</p>
          {nav.map(([key, label]) => (
            <a
              className={route === key ? "active" : ""}
              aria-current={route === key ? "page" : undefined}
              href={`#/${key}`}
              key={key}
              onClick={(event) => { event.preventDefault(); navigate(key); }}
            >
              {label}
            </a>
          ))}
          <div className="sidebar-foot">
            <span>{session.user?.role}</span>
            <small>Ghostwriter delivery is review-gated.</small>
          </div>
        </nav>
        <main className="content" id="main-content" tabIndex={-1}>{connection === "expired" ? <div className="connection-banner" role="alert">Session expired. Server writes are disabled. Save any unsaved draft with <strong>Save draft file</strong>, then sign in again. <button onClick={() => dirty ? setReauthPrompt(true) : reauthenticate()}>Sign in again</button></div> : connection !== "connected" ? <div className="connection-banner" role="status">{connection === "offline" ? "Connection lost." : "Connection not confirmed."} Unsaved text remains in memory. Server writes are disabled. Last sync: {lastSync || "not yet"}.</div> : null}{content}</main>
      </div>
      {pending && <UnsavedDialog label={pending.label} onStay={() => setPending(null)} onDiscard={() => { const action = pending.run; setPending(null); setDirty(false); action(); }} />}
      {reauthPrompt && <Modal titleId="reauth-title" onDismiss={() => setReauthPrompt(false)}><h2 id="reauth-title">Unsaved draft changes</h2><p>Save a draft file before signing in again. Signing in again clears the text on this page.</p><div className="actions"><button data-initial-focus onClick={() => setReauthPrompt(false)}>Stay and save draft file</button><button onClick={reauthenticate}>Sign in again and clear local text</button></div></Modal>}
    </div>
  );
}
export { Shell };
