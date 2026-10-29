// Gate / export diagnostics carry a stable code, an English message and a ref (ADR-247). In English the server
// message is shown as sent (it has the most detail); in another language a known code is translated with its ref
// (`gateDiagnostics.<code>`), anything else - e.g. a plugin's own diagnostic - falls back to the server message.
import i18n from "i18next";

export interface DiagnosticLike { severity: string; code: string; message: string; ref?: string | null }

export function diagnosticMessage(d: DiagnosticLike): string {
  const key = `gateDiagnostics.${d.code}`;
  if (i18n.language === "en" || !i18n.exists(key)) return d.message;
  return String(i18n.t(key as never, { ref: d.ref ?? "" } as never));
}

export function severityLabel(severity: string): string {
  const key = `gateDiagnostics.severity.${severity}`;
  return i18n.exists(key) ? String(i18n.t(key as never)) : severity;
}
