import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { contactsRequest } from "./contacts";
import { ContactList } from "./ContactList";

export function ContactsPanel({ apiBaseUrl, onClose }: { apiBaseUrl: string; onClose: () => void }) {
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const panel = useRef<HTMLElement>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    panel.current?.focus();
    return () => request.current?.abort();
  }, []);
  async function importFile(file: File) {
    if (file.size > 1_000_000) { setStatus("Choose a vCard smaller than 1 MB."); return; }
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setStatus("");
    try {
      const vcard = await file.text();
      if (controller.signal.aborted) return;
      const result = await contactsRequest(apiBaseUrl, "/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vcard }), signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        if (typeof result !== "object" || !result || !("imported" in result) || typeof result.imported !== "number") {
          throw new Error("Invalid import response");
        }
        setStatus(`Imported ${result.imported} contact addresses.`);
        setVersion((value) => value + 1);
      }
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setStatus(reason instanceof Error ? reason.message : "Import failed.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return createPortal(<aside className="invite-inbox contacts-panel" role="dialog" aria-modal="false"
    aria-label="Contacts" ref={panel} tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <button type="button" aria-label="Close contacts" onClick={onClose}>Close</button>
    <h2>Contacts</h2>
    <p>Import your friends to send invitations. Contacts are private to your account.</p>
    <a href={`${apiBaseUrl.replace(/\/$/, "")}/api/contacts/google/start`}>Import Google Contacts</a>
    <label>Import vCard (.vcf)<input type="file" accept=".vcf,text/vcard" disabled={busy}
      onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void importFile(file);
        event.target.value = "";
      }} /></label>
    <p>Phone numbers need an international +country code.</p>
    {busy && <p role="status">Importing contacts…</p>}
    {status && <p role="status">{status}</p>}
    <ContactList key={version} apiBaseUrl={apiBaseUrl} />
  </aside>, document.body);
}
