import { applyChartLibrary } from "../../design/chartLibrary";
import { applyPalette } from "../../design/palette";
import { PrintExport } from "./PrintExport";
import "./print.css";

/** Entry for `/print/*` (US-096): no sign-in, no shell, no API calls — only the payload the PDF renderer injected. */
export function PrintApp() {
  const payload = window.__TELA_EXPORT__;
  // The tenant's colours (US-151) before the charts draw: exports always print in the light theme.
  const palette = payload?.branding?.palette;
  applyPalette(palette ? { light: palette, dark: palette } : null, "export");
  applyChartLibrary(payload?.branding?.chart_library);
  return <PrintExport payload={payload} />;
}
