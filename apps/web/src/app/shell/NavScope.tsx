import type { ReactNode } from "react";

import { useNavColors, type NavPlace, type NavStyle } from "../../design/navColors";
import { ThemeScope } from "../../design/theme";

/** The attributes that colour a bar (`index.css`, US-209). */
export function navAttrs(place: NavPlace, style: NavStyle) {
  return { "data-nav-place": place, "data-nav": style };
}

/** The style a bar uses: the one given (Branding preview) or the tenant's. */
export function useNavStyle(place: NavPlace, style?: NavStyle): NavStyle {
  const colors = useNavColors();
  return style ?? colors[place];
}

/** Draws a dark bar in the dark theme (tokens, logo); other styles need no wrapper. */
export function NavTheme({ style, children }: { style: NavStyle; children: ReactNode }) {
  return style === "dark" ? <ThemeScope theme="dark" className="contents">{children}</ThemeScope> : <>{children}</>;
}
