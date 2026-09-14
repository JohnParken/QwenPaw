import type { CSSProperties } from "react";
import { previewAttemptEntries, type TLPreviewStore } from "../tlPreview";

type TLPreviewProps = {
  store: TLPreviewStore;
};

const shellStyle: CSSProperties = {
  margin: "0 16px 12px",
  padding: "10px 12px",
  border: "1px solid var(--app-border-color, rgba(127, 127, 127, 0.25))",
  borderRadius: 8,
  background: "var(--app-bg-color-secondary, rgba(127, 127, 127, 0.08))",
};

const textStyle: CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  fontFamily: "var(--app-font-family, inherit)",
};

export default function TLPreview({ store }: TLPreviewProps) {
  const attempts = previewAttemptEntries(store);
  if (attempts.length === 0) return null;

  return (
    <div aria-live="polite" aria-label="Generating preview" style={shellStyle}>
      {attempts.map(([key, attempt]) => {
        const text = Object.keys(attempt.items)
          .sort((left, right) => Number(left) - Number(right))
          .map((index) => attempt.items[Number(index)])
          .join("\n");
        if (attempt.kind === "tool_call") {
          return (
            <details key={key}>
              <summary>Generating tool call</summary>
              <pre style={textStyle}>{text}</pre>
            </details>
          );
        }
        return (
          <div key={key}>
            <div>Generating response</div>
            <pre style={textStyle}>{text}</pre>
          </div>
        );
      })}
    </div>
  );
}
