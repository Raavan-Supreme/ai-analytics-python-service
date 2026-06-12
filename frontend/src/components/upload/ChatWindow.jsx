import { useEffect, useMemo, useState } from "react";

export default function ChatWindow({ onSend, pending, files = [], connectors = [] }) {
  const [text, setText] = useState("");
  const [sourceType, setSourceType] = useState("all");
  const [sourceId, setSourceId] = useState("");

  const sourceOptions = useMemo(() => {
    if (sourceType === "sheet") {
      return files.map((file) => ({ value: String(file.id), label: file.originalName || `Sheet ${file.id}` }));
    }
    if (sourceType === "connector") {
      return connectors.map((connector) => ({ value: String(connector.id), label: connector.name || `Connector ${connector.id}` }));
    }
    return [];
  }, [connectors, files, sourceType]);

  useEffect(() => {
    console.log("[AISX][ChatWindow] source inputs", {
      sourceType,
      filesCount: files.length,
      connectorsCount: connectors.length,
      sourceOptionsCount: sourceOptions.length,
      sourceOptions,
      sourceId
    });
  }, [connectors, files, sourceId, sourceOptions, sourceType]);

  useEffect(() => {
    if (sourceType === "all") {
      if (sourceId) setSourceId("");
      return;
    }
    if (!sourceOptions.some((option) => option.value === sourceId)) {
      console.warn("[AISX][ChatWindow] sourceId reset due to missing option", {
        sourceType,
        sourceId,
        available: sourceOptions
      });
      setSourceId("");
    }
  }, [sourceId, sourceOptions, sourceType]);

  const requiresSpecificSource = sourceType === "sheet" || sourceType === "connector";
  const canSubmit = text.trim() && (!requiresSpecificSource || Boolean(sourceId));

  const submit = (event) => {
    event.preventDefault();
    if (!canSubmit) {
      console.warn("[AISX][ChatWindow] submit blocked", {
        text,
        sourceType,
        sourceId,
        requiresSpecificSource,
        sourceOptionsCount: sourceOptions.length
      });
      return;
    }
    console.log("[AISX][ChatWindow] submit", {
      question: text.trim(),
      sourceType,
      sourceId: sourceId || null
    });
    onSend({
      question: text.trim(),
      sourceType,
      sourceId: sourceId || null
    });
    setText("");
  };

  return (
    <div className="panel">
      <h3>Ask Your Data</h3>
      <p>Answers appear in the Result panel on the right after you click Run.</p>
      <form onSubmit={submit} className="inline-form">
        <select
          value={sourceType}
          onChange={(e) => {
            setSourceType(e.target.value);
            setSourceId("");
          }}
        >
          <option value="all">All Sources</option>
          <option value="sheet">Specific Sheet ({files.length})</option>
          <option value="connector">Specific Database ({connectors.length})</option>
        </select>
        {(sourceType === "sheet" || sourceType === "connector") && (
          <select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            disabled={sourceOptions.length === 0}
          >
            <option value="">Select source</option>
            {sourceOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        )}
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Example: show top 10 products by revenue"
        />
        <button className="btn-primary" disabled={pending || !canSubmit}>{pending ? "Running..." : "Run"}</button>
      </form>
      {requiresSpecificSource && !sourceId && (
        <p>Select a {sourceType === "sheet" ? "sheet" : "database connector"} to run this question.</p>
      )}
    </div>
  );
}
