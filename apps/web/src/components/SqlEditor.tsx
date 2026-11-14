import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { MSSQL, MySQL, PostgreSQL, sql, StandardSQL } from "@codemirror/lang-sql";
import { bracketMatching, HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { Decoration, EditorView, keymap, lineNumbers, MatchDecorator, placeholder as placeholderExt, ViewPlugin, type DecorationSet, type ViewUpdate } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

const DIALECTS = { postgres: PostgreSQL, mysql: MySQL, sqlserver: MSSQL, excel: StandardSQL, csv: StandardSQL } as const;

/** `:page_filters` stands for the dashboard's filters (REQ-080); it stands out in the editor. */
const placeholderMark = new MatchDecorator({ regexp: /:page_filters\b/g, decoration: Decoration.mark({ class: "cm-page-filters" }) });
const pageFilters = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = placeholderMark.createDeco(view); }
  update(u: ViewUpdate) { this.decorations = placeholderMark.updateDeco(u, this.decorations); }
}, { decorations: (v) => v.decorations });

// Colours come from the design tokens, so the editor follows light and dark themes. CodeMirror mounts these rules as
// a constructable stylesheet, which the strict CSP (style-src 'self') allows.
const theme = EditorView.theme({
  "&": { backgroundColor: "var(--tl-surface-raised)", color: "var(--tl-ink)", fontSize: "13px", border: "1px solid var(--tl-line-strong)", borderRadius: "var(--tl-radius-md)" },
  "&.cm-focused": { outline: "none", boxShadow: "var(--tl-focus-ring)" },
  ".cm-content": { fontFamily: "var(--tl-font-mono)", padding: "8px 0", caretColor: "var(--tl-ink)" },
  ".cm-scroller": { minHeight: "12rem", maxHeight: "28rem" },
  ".cm-gutters": { backgroundColor: "var(--tl-surface-sunken)", color: "var(--tl-ink-muted)", border: "none", borderRadius: "var(--tl-radius-md) 0 0 var(--tl-radius-md)" },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "transparent" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": { backgroundColor: "var(--tl-accent-soft) !important" },
  ".cm-page-filters": { backgroundColor: "var(--tl-personal-soft)", color: "var(--tl-personal)", borderRadius: "3px", fontWeight: "600" },
  ".cm-placeholder": { color: "var(--tl-ink-muted)" },
  ".cm-tooltip": { backgroundColor: "var(--tl-surface-raised)", border: "1px solid var(--tl-line)", color: "var(--tl-ink)" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--tl-accent-soft)", color: "var(--tl-ink)" },
});
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword], color: "var(--tl-accent)", fontWeight: "600" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--tl-chart-2)" },
  { tag: [tags.number, tags.bool, tags.null], color: "var(--tl-chart-3)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "var(--tl-ink-muted)", fontStyle: "italic" },
  { tag: [tags.typeName, tags.standard(tags.name)], color: "var(--tl-chart-4)" },
]);

export interface SqlEditorHandle {
  /** Inserts text at the cursor (a table or column picked from the schema list). */
  insert: (text: string) => void;
  focus: () => void;
}

/**
 * SQL editor on CodeMirror 6 (US-080): dialect-aware highlighting and completion of the tables and columns the person
 * may use, `:page_filters` marked, undo history, Tab indents (Escape then Tab leaves the editor).
 */
export const SqlEditor = forwardRef<SqlEditorHandle, {
  value: string;
  onChange: (sql: string) => void;
  engine?: string;
  schema?: Record<string, string[]>;
  label: string;
  describedBy?: string;
  placeholder?: string;
}>(function SqlEditor({ value, onChange, engine = "postgres", schema, label, describedBy, placeholder }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;

  useEffect(() => {
    const dialect = DIALECTS[engine as keyof typeof DIALECTS] ?? StandardSQL;
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(), history(), bracketMatching(), closeBrackets(), autocompletion(),
          keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
          sql({ dialect, schema, upperCaseKeywords: true }), syntaxHighlighting(highlight), theme, pageFilters,
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label, ...(describedBy ? { "aria-describedby": describedBy } : {}) }),
          ...(placeholder ? [placeholderExt(placeholder)] : []),
          EditorView.updateListener.of((u) => { if (u.docChanged) change.current(u.state.doc.toString()); }),
        ],
      }),
    });
    view.current = v;
    return () => v.destroy();
    // The editor is created once per dialect and schema; outside changes to `value` are applied below.
  }, [engine, schema]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  useImperativeHandle(ref, () => ({
    insert: (text) => {
      const v = view.current;
      if (!v) return;
      const { from, to } = v.state.selection.main;
      v.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
      v.focus();
    },
    focus: () => view.current?.focus(),
  }), []);

  return <div ref={host} data-slot="sql-editor" className="min-w-0" />;
});
