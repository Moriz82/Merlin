import React from "react";
import { Modal } from "./Modal";
function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <strong>Could not complete that request.</strong>
      <span>
        {error instanceof Error
          ? error.message
          : "The service returned an unreadable error."}
      </span>
    </div>
  );
}
function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}
function UnsavedDialog({ label, onStay, onDiscard }: { label: string; onStay: () => void; onDiscard: () => void }) {
  return <Modal titleId="unsaved-title" onDismiss={onStay}><h2 id="unsaved-title">Work is not saved</h2><p>Save the draft or record the question before {label}, or discard the local text.</p><div className="actions"><button data-initial-focus className="primary" onClick={onStay}>Stay and edit</button><button onClick={onDiscard}>Discard and continue</button></div></Modal>;
}
export { ErrorMessage, Empty, UnsavedDialog };
