import type { TestResult } from "../../../api/app";
import { Badge } from "../../../components/ui/badge";
import { StatusBadge } from "../../../components/ui/status-badge";

/** Test results as a checklist: what passed, what failed and what to do about it (MSG-002 – MSG-004). */
const SSH_FAILED: Record<string, (who: string) => string> = {
  connect: (who) => `Could not reach ${who}.`,
  host_key: (who) => `${who[0]!.toUpperCase()}${who.slice(1)} could not be verified: its host key or certificate does not match.`,
  auth: (who) => `${who[0]!.toUpperCase()}${who.slice(1)} rejected the user name or key.`,
  forward: () => "The SSH server could not reach the database.",
};

/** "the SSH server" or "jump host bastion-1.example.com" — which hop a result is about (ENH-004). */
function hopName(r: TestResult): string {
  const hop = r.ssh?.hops?.find((h) => h.id === r.ssh?.hop);
  return r.ssh?.hop && hop ? `jump host ${hop.host}` : "the SSH server";
}

export function TestChecklist({ r }: { r: TestResult }) {
  const rows: [boolean, string, string][] = [
    ...(r.ssh ? [[r.ssh.ok, (r.ssh.hops?.length ?? 1) > 1 ? `Opened the SSH tunnel through ${r.ssh.hops!.length} hops${r.ssh.pinned ? "; pinned new host keys" : ""}`
      : r.ssh.pinned ? "Opened the SSH tunnel and pinned the server's host key" : "Opened the SSH tunnel",
      SSH_FAILED[r.ssh.stage ?? ""]?.(hopName(r)) ?? "Could not open the SSH tunnel."] as [boolean, string, string]] : []),
    [r.reachable, `Reached the server${r.latency_ms !== null ? ` (${r.latency_ms} ms)` : ""}`, "Could not reach the server. Check the host, port and network path."],
    [r.authenticated, "Signed in", "The user name or password was rejected."],
    [r.read_only.verdict === "safe", "Cannot write: the account is read-only", "This account can change data. Use a read-only account."],
  ];
  const first = rows.findIndex(([ok]) => !ok);
  return (
    <div className="grid gap-2" role="status">
      <ul className="grid gap-2">
        {rows.map(([ok, yes, no], i) => (
          // Checks after the first failure were not run, so they are shown as not checked.
          <li key={yes} className="flex items-start gap-2">
            {first !== -1 && i > first ? <Badge variant="neutral">Not checked</Badge> : <StatusBadge status={ok ? "ok" : "danger"}>{ok ? "Passed" : "Failed"}</StatusBadge>}
            <span>{ok || (first !== -1 && i > first) ? yes : no}</span>
          </li>
        ))}
      </ul>
      {r.server_version && <p className="text-small text-ink-muted">PostgreSQL {r.server_version}</p>}
      {r.ssh?.hops && r.ssh.hops.length > 1 ? (
        <ul className="grid gap-1 text-small text-ink-muted" aria-label="SSH hops">
          {r.ssh.hops.map((h) => (
            <li key={h.id || "server"}>{h.id ? "Jump host" : "SSH server"} {h.host}:{h.port} · {h.verified === "ca" ? "host certificate from the trusted CA" : <>host key <span className="break-all font-mono">{h.host_key}</span></>}</li>
          ))}
        </ul>
      ) : r.ssh?.host_key && <p className="text-small text-ink-muted">SSH host key <span className="break-all font-mono">{r.ssh.host_key}</span></p>}
      {r.message && <p className="text-status-danger">{r.message}</p>}
      {r.read_only.reasons.length > 0 && <ul className="list-disc pl-5 text-small">{r.read_only.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
    </div>
  );
}
