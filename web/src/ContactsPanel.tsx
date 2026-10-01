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
    <details>
      <summary>Import iPhone/iCloud contacts</summary>
      <ol>
        <li>On your iPhone, open Contacts and tap Lists.</li>
        <li>Press and hold your iCloud list, then tap Export.</li>
        <li>Include names, phone numbers, and email addresses, then tap Done.</li>
        <li>Choose Save to Files, then upload the saved .vcf file below.</li>
      </ol>
      <p>You can also export a vCard from iCloud.com Contacts on a tablet or computer.</p>
      <a href="https://support.apple.com/guide/iphone/export-contacts-iph075ddebf2/ios"
        target="_blank" rel="noreferrer">Apple’s contact export instructions</a>
      <p>This imports a copy of your contacts. To update them later, export and import again.</p>
    </details>
    <label>Import vCard (.vcf)<input type="file" accept=".vcf,text/vcard" disabled={busy}
      onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void importFile(file);
        event.target.value = "";
      }} /></label>
    <p>Numbers without a country code default to +1 (US/Canada and other +1 regions). For other countries, include + and the country code. Include the area code; extensions are not supported.</p>
    {busy && <p role="status">Importing contacts…</p>}
    {status && <p role="status">{status}</p>}
    <ContactList key={version} apiBaseUrl={apiBaseUrl} />
  </aside>, document.body);
}
