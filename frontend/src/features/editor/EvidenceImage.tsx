import { useState } from 'react'

export function EvidenceImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false)
  return failed ? <span className="omitted-image" role="note">Evidence image could not load. The selected evidence remains attached to this draft.</span> : <img className="evidence-image" src={src} alt={alt} onError={() => setFailed(true)} />
}
