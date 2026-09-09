import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { EvidenceImage } from "./editor/EvidenceImage";
const evidencePath = /^\/api\/evidence\/([0-9a-f]{8}-[0-9a-f-]{27,})\/render$/i;
function safeEvidencePath(value: string, allowed: Set<string>) {
  const match = evidencePath.exec(value);
  return Boolean(match && allowed.has(match[1]));
}
function Markdown({ value, evidenceIds = [] }: { value: string; evidenceIds?: string[] }) {
  const allowed = new Set(evidenceIds);
  return (
    <div className="markdown">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => href && (href.startsWith('#') || safeEvidencePath(href, allowed)) ? <a href={href}>{children}</a> : <span className="unsafe-link" role="note">Unsafe link omitted.</span>,
          img: ({ src, alt }) => src && safeEvidencePath(src, allowed) ? <EvidenceImage key={src} src={src} alt={alt ?? 'Reviewed evidence'} /> : <span className="omitted-image" role="note">Embedded image omitted. Attach reviewed evidence instead.</span>,
        }}
      >
        {value || "*Nothing written yet.*"}
      </ReactMarkdown>
    </div>
  );
}
export { Markdown };
