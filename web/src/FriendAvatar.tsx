import { useState } from "react";
import { avatarUrl, type FriendPerson } from "./friends";

export function FriendAvatar({ person }: { person: FriendPerson }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const url = avatarUrl(person.avatar_url);
  return url && failedUrl !== url
    ? <img className="friend-avatar" src={url} alt="" referrerPolicy="no-referrer"
        onError={() => setFailedUrl(url)} />
    : <span className="friend-avatar friend-initial" aria-hidden="true">{person.display_name.slice(0, 1)}</span>;
}
