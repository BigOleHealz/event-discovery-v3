import { useEffect, useState } from "react";
import { listContacts, type Contact } from "./contacts";

export function ContactList({ apiBaseUrl, selected, onToggle, disabled = false }: {
  apiBaseUrl: string;
  selected?: string[];
  onToggle?: (id: string) => void;
  disabled?: boolean;
}) {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      void listContacts(apiBaseUrl, search, offset, controller.signal).then((rows) => {
        if (!controller.signal.aborted) setContacts(rows);
      }).catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load contacts.");
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [apiBaseUrl, search, offset]);
  return <section className="contact-list" aria-label="Your contacts">
    <label>Search contacts<input type="search" value={search} disabled={disabled}
      onChange={(event) => { setSearch(event.target.value); setOffset(0); }} /></label>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading contacts…</p> : !error && <>
      {contacts.length === 0 && <p>No contacts found. Import contacts from your account menu.</p>}
      <ul>{contacts.map((contact) => <li key={contact.id}>
        <label>{onToggle && <input type="checkbox" checked={selected?.includes(contact.id) ?? false}
          disabled={disabled || (!contact.matched_user_id && !contact.phone_e164)}
          onChange={() => onToggle(contact.id)} />}
          <span>{contact.display_name ?? contact.email ?? contact.phone_e164}
            <small>{contact.phone_e164 ?? contact.email} · {contact.matched_user_id ? "In app" : contact.phone_e164 ? "SMS" : "Needs a phone number"}</small>
          </span>
        </label>
      </li>)}</ul>
      <nav aria-label="Contact pages">
        <button type="button" disabled={disabled || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - 20))}>Previous contacts</button>
        <button type="button" disabled={disabled || contacts.length < 20} onClick={() => setOffset((value) => value + 20)}>Next contacts</button>
      </nav>
    </>}
  </section>;
}
