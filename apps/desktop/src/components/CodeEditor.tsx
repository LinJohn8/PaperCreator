/**
 * CodeMirror 6 wrapper.
 *
 * Two behaviours worth knowing:
 *
 * * **The editor owns its content while focused.** Pushing `value` into the view
 *   on every keystroke would fight the user's cursor; the value is only
 *   reconciled when it differs from the document *and* the change came from
 *   outside (a save, an agent write, a version restore).
 * * **Citation markers are highlighted.** `[SCARSELLI2009]` is the app's own
 *   syntax, and seeing it distinct from prose is what makes a drafted section
 *   readable - and makes a fabricated marker visible.
 */

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { searchKeymap } from "@codemirror/search";
import {
  Annotation,
  EditorState,
  RangeSetBuilder,
  StateField,
  Transaction,
} from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  placeholder as placeholderExt,
} from "@codemirror/view";
import { oneDark } from "@codemirror/theme-one-dark";
import { tags } from "@lezer/highlight";
import { useEffect, useRef } from "react";

const CITATION = /\[([A-Za-z][A-Za-z0-9]{2,24})\]/g;

const citationMark = Decoration.mark({ class: "cm-citation" });
const externalValue = Annotation.define<boolean>();

/** Highlight every `[KEY]` citation marker in the visible document. */
const citationField = StateField.define<DecorationSet>({
  create(state) {
    return buildCitationDecorations(state);
  },
  update(value, transaction) {
    if (!transaction.docChanged) return value.map(transaction.changes);
    return buildCitationDecorations(transaction.state);
  },
  provide: (field) => EditorView.decorations.from(field),
});

function buildCitationDecorations(state: EditorState): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const text = state.doc.toString();
  // Cheap enough for a section-sized document; a viewport-only pass would be
  // needed for very large files, which sections are not.
  for (const match of text.matchAll(CITATION)) {
    if (match.index === undefined) continue;
    builder.add(match.index, match.index + match[0].length, citationMark);
  }
  return builder.finish();
}

const editorTheme = EditorView.theme({
  "&": { backgroundColor: "transparent", height: "100%" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.65" },
  ".cm-content": { padding: "14px 18px", caretColor: "var(--fg-strong)" },
  ".cm-line": { padding: "0 2px" },
  ".cm-citation": {
    color: "var(--ok)",
    backgroundColor: "rgba(78, 201, 176, 0.12)",
    borderRadius: "2px",
    padding: "0 1px",
  },
  ".cm-activeLine": { backgroundColor: "rgba(255,255,255,0.03)" },
  ".cm-selectionBackground, ::selection": { backgroundColor: "#264f78 !important" },
  ".cm-placeholder": { color: "var(--fg-dim)", fontStyle: "italic" },
});

const proseHighlight = HighlightStyle.define([
  { tag: tags.heading, color: "var(--fg-strong)", fontWeight: "600" },
  { tag: tags.strong, color: "var(--fg-strong)", fontWeight: "600" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, color: "var(--link)" },
  { tag: tags.monospace, color: "var(--modified)" },
  { tag: tags.quote, color: "var(--fg-muted)", fontStyle: "italic" },
]);

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  onSelectionChange?: (text: string) => void;
  placeholder?: string;
  readOnly?: boolean;
}

export function CodeEditor({ value, onChange, onSave, onSelectionChange, placeholder, readOnly }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  // Read callbacks through a ref so a parent re-render does not rebuild the view
  // and lose undo history and cursor position.
  const callbacks = useRef({ onChange, onSave, onSelectionChange });
  callbacks.current = { onChange, onSave, onSelectionChange };

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions: [
          history(),
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => {
                callbacks.current.onSave?.();
                return true;
              },
            },
            indentWithTab,
            ...defaultKeymap,
            ...historyKeymap,
            ...searchKeymap,
          ]),
          markdown({ base: markdownLanguage }),
          syntaxHighlighting(proseHighlight),
          oneDark,
          editorTheme,
          citationField,
          EditorView.lineWrapping,
          EditorState.readOnly.of(Boolean(readOnly)),
          placeholderExt(placeholder ?? ""),
          EditorView.updateListener.of((update) => {
            const isExternalReconciliation = update.transactions.some((transaction) =>
              transaction.annotation(externalValue),
            );
            if (update.docChanged && !isExternalReconciliation) {
              callbacks.current.onChange(update.state.doc.toString());
            }
            if (update.selectionSet || update.docChanged) {
              const range = update.state.selection.main;
              callbacks.current.onSelectionChange?.(
                range.empty ? "" : update.state.sliceDoc(range.from, range.to),
              );
            }
          }),
        ],
      }),
      parent: host,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly]);

  // Reconcile external changes only. Replacing the document while the user types
  // would reset the cursor to the start on every keystroke.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    if (view.hasFocus) return;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
      annotations: [externalValue.of(true), Transaction.addToHistory.of(false)],
    });
  }, [value]);

  return <div className="cm-host" ref={hostRef} />;
}
