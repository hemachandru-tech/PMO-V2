import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type { Me } from "../api/types";

export function useMe() {
  return useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/v1/me"),
    retry: (count, err) => !(err instanceof ApiError && [401, 403].includes(err.status)) && count < 2,
    staleTime: 60_000,
  });
}

export function can(me: Me | undefined, permission: string): boolean {
  return !!me && me.permissions.includes(permission);
}
