import React, { useEffect, useRef, useState } from "react";
import { ApiError, api, downloadJson, RecordItem, Session } from "../api";
import { ErrorMessage, Empty, UnsavedDialog } from "../components/common";
import { Shell } from "./Shell";
function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<Session>("/api/login", { name, password });
      api.csrf = result.csrf;
      onLogin(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-card">
        <p className="eyebrow">Merlin / report writing desk</p>
        <h1>Make the prose accountable.</h1>
        <p className="muted">
          A private lead queue for evidence-backed drafts and reviewed
          Ghostwriter delivery.
        </p>
        <form onSubmit={submit}>
          <label>
            Account name
            <input
              required
              autoComplete="username"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Password
            <input
              required
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <ErrorMessage error={error} />
          <button className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <small>LAN mode · drafts stay in the engagement</small>
      </div>
    </main>
  );
}
function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    void api
      .get<Session>("/api/session")
      .then((value) => {
        api.csrf = value.csrf;
        setSession(value);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) setSession(null);
        else setError(err);
      })
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="loading">Checking session…</div>;
  if (error)
    return (
      <main className="login">
        <div className="login-card">
          <ErrorMessage error={error} />
          <p className="muted">
            The LAN service is not ready. Check the application status and
            reload.
          </p>
        </div>
      </main>
    );
  return session?.user ? (
    <Shell
      session={session}
      onLogout={() => setSession({ ...session, user: null })}
    />
  ) : (
    <Login onLogin={setSession} />
  );
}
export { App, Login };
