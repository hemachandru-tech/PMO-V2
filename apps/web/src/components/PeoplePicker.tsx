import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "./ui/command";

export type Person = { id: string; name: string };

/**
 * People combobox (US-142): type a name, arrows to choose, Enter to add. Names come from the directory search
 * (active colleagues, name only); `exclude` hides people already chosen or already shared with.
 */
export function PeoplePicker({ label, onPick, exclude = [], placeholder = "Type a name", admin = false, single = false, pickHint = "Enter to add" }: {
  label: string;
  onPick: (p: Person) => void;
  exclude?: string[];
  placeholder?: string;
  /** Search every account (Admin user list, needs `mappings.manage`) instead of the colleague directory. */
  admin?: boolean;
  /** Choose one person: the list closes and the name stays in the box (otherwise it clears for the next one). */
  single?: boolean;
  pickHint?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const people = useQuery({
    queryKey: [admin ? "admin-users" : "directory", q],
    queryFn: async () => (admin
      ? { items: (await api<{ items: { id: string; display_name: string }[] }>(`/api/v1/admin/users?q=${encodeURIComponent(q)}`)).items.map((u) => ({ id: u.id, name: u.display_name })) }
      : api<{ items: Person[] }>(`/api/v1/directory/users?q=${encodeURIComponent(q)}`)),
    enabled: open,
  });
  const options = (people.data?.items ?? []).filter((p) => !exclude.includes(p.id)).slice(0, 8);
  return (
    <div className="grid gap-1">
      {/* cmdk names the input through its own hidden label (`label`), so the visible caption is decorative. */}
      <span aria-hidden className="text-body-strong">{label}</span>
      <Command label={label} shouldFilter={false} className="relative overflow-visible border border-line-strong" onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}>
        <CommandInput value={q} placeholder={placeholder} autoComplete="off"
          onValueChange={(v) => { setQ(v); setOpen(true); }} onFocus={() => setOpen(true)}
          // Tab leaves the picker: close the suggestions first, or the focus lands in the closing list and is lost.
          onKeyDown={(e) => { if (e.key === "Escape" || e.key === "Tab") setOpen(false); }} />
        {/* An overlay, so opening and closing never moves what is below (a click there would miss).
            Not a tab stop (Chrome makes scrolling boxes focusable); arrows move through it from the input.
            Kept mounted, hidden and empty while closed: the input's aria-controls must point at it (axe). */}
        <CommandList aria-label={`${label}: suggestions`} tabIndex={-1} hidden={!open}
          className="absolute inset-x-0 top-full z-50 mt-1 max-h-56 rounded-md border border-line bg-surface-raised shadow-overlay">
          {open && !people.isLoading && <CommandEmpty>{q ? "Nobody matches that name." : "No colleagues to add."}</CommandEmpty>}
          {open && options.map((p, i) => (
            <CommandItem key={p.id} value={p.id} onSelect={() => { onPick(p); setQ(single ? p.name : ""); if (single) setOpen(false); }}>
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              {i === 0 && <span className="text-small text-ink-muted" aria-hidden>{pickHint}</span>}
            </CommandItem>
          ))}
        </CommandList>
      </Command>
    </div>
  );
}
