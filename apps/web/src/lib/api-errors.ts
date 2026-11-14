import type { FieldValues, Path, UseFormReturn } from "react-hook-form";

import { ApiError } from "../api/client";
import { appName } from "../app/branding";

/** Plain-language wording for error codes whose API message is too technical for the person reading it. */
const FRIENDLY: Record<string, () => string> = {
  precondition_failed: () => "Someone else changed this since you loaded it. Reload and try again.",
  unknown_group: () => `Some groups are not known to ${appName()} yet. Register them first.`,
  network_error: () => `${appName()} could not be reached. Check your connection and try again.`,
};

export function friendlyMessage(error: ApiError): string {
  return FRIENDLY[error.code]?.() ?? error.message;
}

/** Anything thrown by a request, as an ApiError (network failures and bugs included) so it can be shown the same way. */
export function asApiError(e: unknown, fallback = "Something went wrong."): ApiError {
  if (e instanceof ApiError) return e;
  if (e instanceof TypeError) return new ApiError(0, "network_error", FRIENDLY.network_error!(), null);
  return new ApiError(0, "error", fallback, null);
}

export interface ServerErrorMapping {
  /** API body path (dot-joined, without "body") → form field, e.g. `{ approved_schemas: "schemas", "tls.mode": "tls" }`. */
  fields?: Record<string, string>;
  /** Error code → form field, for business rules that concern one field (e.g. a duplicate name). */
  codes?: Record<string, string>;
}

function sentence(msg: string): string {
  const t = msg.trim().replace(/^Value error, /, "");
  return t ? t[0]!.toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") : t;
}

/**
 * Splits an API error into field errors and the part that belongs in the form-level alert (US-135).
 * Validation errors carry `details.errors[].loc` (e.g. ["body", "port"]); other errors can name `details.field`.
 */
export function splitServerError(error: ApiError, known: readonly string[], mapping: ServerErrorMapping = {}) {
  const fieldErrors: { field: string; message: string }[] = [];
  let unmapped = 0;
  const place = (path: string[]): string | null => {
    for (let n = path.length; n > 0; n--) {
      const key = path.slice(0, n).join(".");
      const target = mapping.fields?.[key] ?? key;
      if (known.includes(target)) return target;
    }
    return null;
  };
  const errors = error.details.errors;
  if (error.code === "validation_error" && Array.isArray(errors)) {
    for (const e of errors as { loc?: unknown[]; msg?: string }[]) {
      const loc = (e.loc ?? []).map(String);
      const path = ["body", "query", "path"].includes(loc[0] ?? "") ? loc.slice(1) : loc;
      const field = place(path);
      if (field) fieldErrors.push({ field, message: sentence(e.msg ?? error.message) });
      else unmapped++;
    }
  } else {
    const named = typeof error.details.field === "string" ? place(error.details.field.split(".")) : null;
    const field = named ?? mapping.codes?.[error.code] ?? null;
    if (field && known.includes(field)) fieldErrors.push({ field, message: error.message });
    else unmapped++;
  }
  return { fieldErrors, formLevel: unmapped > 0 || fieldErrors.length === 0 };
}

/** Puts an API error on the form: field errors under their fields; returns the ApiError for the alert, if any. */
export function applyServerError<T extends FieldValues>(
  form: UseFormReturn<T>, e: unknown, mapping: ServerErrorMapping = {},
): ApiError | null {
  const error = asApiError(e);
  const known = Object.keys(form.getValues());
  const { fieldErrors, formLevel } = splitServerError(error, known, mapping);
  fieldErrors.forEach(({ field, message }, i) =>
    form.setError(field as Path<T>, { type: "server", message }, { shouldFocus: i === 0 }));
  return formLevel ? error : null;
}
