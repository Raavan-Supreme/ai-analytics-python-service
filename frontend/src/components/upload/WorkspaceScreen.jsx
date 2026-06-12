import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { connectorApi, extractList, fileApi, parseApiError, queryApi } from "../../services/api";
import ChatWindow from "./ChatWindow";
import ResultPanel from "./ResultPanel";
import ExplainabilityDrawer from "./ExplainabilityDrawer";

const CONNECTOR_CACHE_PREFIX = "connectors-cache-";

const isObject = (value) => value !== null && typeof value === "object";

const mergeById = (left = [], right = []) => {
  const combined = [...left, ...right].filter((item) => isObject(item));
  const deduped = [];
  for (const item of combined) {
    const id = item?.id;
    if (id != null) {
      if (deduped.some((candidate) => candidate?.id === id)) continue;
      deduped.push(item);
      continue;
    }
    const fingerprint = `${item?.name || ""}|${item?.host || ""}|${item?.database || ""}`;
    if (deduped.some((candidate) => `${candidate?.name || ""}|${candidate?.host || ""}|${candidate?.database || ""}` === fingerprint)) {
      continue;
    }
    deduped.push(item);
  }
  return deduped;
};

const readConnectorCache = (tenantId) => {
  if (!tenantId) return [];
  const key = `${CONNECTOR_CACHE_PREFIX}${tenantId}`;
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const writeConnectorCache = (tenantId, connectorsList) => {
  if (!tenantId) return;
  const key = `${CONNECTOR_CACHE_PREFIX}${tenantId}`;
  localStorage.setItem(key, JSON.stringify(Array.isArray(connectorsList) ? connectorsList : []));
};

const tryParseJson = (text) => {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
};

const normalizePayload = (payload) => {
  if (typeof payload !== "string") return payload;

  const trimmed = payload.trim();
  const direct = tryParseJson(trimmed);
  if (direct) return direct;

  // Handle malformed concatenated payloads like: {...}{"success":false,...}
  const cutIndex = trimmed.indexOf("}{\"success\":false");
  if (cutIndex > 0) {
    const firstChunk = trimmed.slice(0, cutIndex + 1);
    const parsedFirst = tryParseJson(firstChunk);
    if (parsedFirst) return parsedFirst;
  }

  // Last-resort extraction for payloads that still contain a parseable `data` array fragment.
  const dataMatch = trimmed.match(/"data"\s*:\s*(\[[\s\S]*?\])/);
  if (dataMatch?.[1]) {
    const parsedData = tryParseJson(dataMatch[1]);
    if (Array.isArray(parsedData)) {
      return { data: parsedData };
    }
  }

  return payload;
};

const pickList = (payload, preferredKeys = []) => {
  const normalized = normalizePayload(payload);
  if (Array.isArray(normalized?.data)) return normalized.data;
  if (Array.isArray(normalized?.content)) return normalized.content;
  if (Array.isArray(normalized?.items)) return normalized.items;
  if (Array.isArray(normalized)) return normalized;
  return extractList(normalized, preferredKeys);
};

export default function WorkspaceScreen({ tenant }) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [files, setFiles] = useState([]);
  const [connectors, setConnectors] = useState([]);

  const loadSources = useCallback(async () => {
    if (!tenant?.id) return;
    console.log("[AISX][WorkspaceScreen] loadSources:start", { tenantId: tenant.id });
    const cachedConnectors = readConnectorCache(tenant.id);
    console.log("[AISX][WorkspaceScreen] loadSources:cache", {
      tenantId: tenant.id,
      cachedCount: cachedConnectors.length,
      cachedConnectors
    });
    if (cachedConnectors.length > 0) {
      setConnectors((prev) => mergeById(prev, cachedConnectors));
    }

    const [filesRes, connectorsRes] = await Promise.allSettled([
      fileApi.list(tenant.id),
      connectorApi.list(tenant.id)
    ]);

    console.log("[AISX][WorkspaceScreen] loadSources:responses", {
      tenantId: tenant.id,
      filesStatus: filesRes.status,
      connectorsStatus: connectorsRes.status,
      filesPayloadType: filesRes.status === "fulfilled" ? typeof filesRes.value?.data : "error",
      connectorsPayloadType: connectorsRes.status === "fulfilled" ? typeof connectorsRes.value?.data : "error",
      filesPayload: filesRes.status === "fulfilled" ? filesRes.value?.data : filesRes.reason,
      connectorsPayload: connectorsRes.status === "fulfilled" ? connectorsRes.value?.data : connectorsRes.reason
    });

    if (filesRes.status === "fulfilled") {
      const payload = filesRes.value?.data;
      const extractedFiles = pickList(payload, ["files", "data"]);
      console.log("[AISX][WorkspaceScreen] loadSources:files", {
        tenantId: tenant.id,
        extractedCount: extractedFiles.length,
        extractedFiles,
        payload
      });
      setFiles(extractedFiles);
    } else {
      setFiles([]);
      console.error("[AISX][WorkspaceScreen] loadSources:files-error", {
        tenantId: tenant.id,
        reason: filesRes.reason,
        response: filesRes.reason?.response?.data
      });
      toast.error(parseApiError(filesRes.reason, "Failed to load sheets"));
    }

    if (connectorsRes.status === "fulfilled") {
      const payload = connectorsRes.value?.data;
      const loaded = pickList(payload, ["connectors", "data"]);
      console.log("[AISX][WorkspaceScreen] loadSources:connectors-raw", {
        tenantId: tenant.id,
        loadedCount: loaded.length,
        loaded,
        payload
      });
      const merged = mergeById(loaded, cachedConnectors);
      console.log("[AISX][WorkspaceScreen] loadSources:connectors-final", {
        tenantId: tenant.id,
        mergedCount: merged.length,
        merged
      });
      setConnectors(merged);
      writeConnectorCache(tenant.id, merged);
    } else {
      console.error("[AISX][WorkspaceScreen] loadSources:connectors-error", {
        tenantId: tenant.id,
        reason: connectorsRes.reason,
        response: connectorsRes.reason?.response?.data
      });
      if (cachedConnectors.length === 0) {
        setConnectors([]);
      }
      toast.error(parseApiError(connectorsRes.reason, "Failed to load connectors"));
    }

    console.log("[AISX][WorkspaceScreen] loadSources:done", {
      tenantId: tenant.id,
      filesCount: files.length,
      connectorsCount: connectors.length
    });
  }, [tenant?.id]);

  useEffect(() => {
    console.log("[AISX][WorkspaceScreen] mount/effect: tenant changed", { tenantId: tenant?.id });
    loadSources();
  }, [loadSources]);

  useEffect(() => {
    const refreshSources = (event) => {
      const targetTenantId = event?.detail?.tenantId;
      const incomingConnectors = extractList(event?.detail, ["connectors"]);
      console.log("[AISX][WorkspaceScreen] event:sources-updated", {
        tenantId: tenant?.id,
        targetTenantId,
        incomingCount: incomingConnectors.length,
        detail: event?.detail
      });
      if (incomingConnectors.length > 0 && Number(targetTenantId) === Number(tenant?.id)) {
        setConnectors((prev) => {
          const merged = mergeById(prev, incomingConnectors);
          console.log("[AISX][WorkspaceScreen] event:merged-connectors", {
            tenantId: tenant?.id,
            previousCount: prev.length,
            mergedCount: merged.length,
            merged
          });
          writeConnectorCache(tenant.id, merged);
          return merged;
        });
      }
      if (!targetTenantId || Number(targetTenantId) === Number(tenant?.id)) {
        loadSources();
      }
    };

    window.addEventListener("sources-updated", refreshSources);
    return () => window.removeEventListener("sources-updated", refreshSources);
  }, [loadSources, tenant?.id]);

  useEffect(() => {
    console.log("[AISX][WorkspaceScreen] state:files", {
      tenantId: tenant?.id,
      count: files.length,
      files
    });
  }, [files, tenant?.id]);

  useEffect(() => {
    console.log("[AISX][WorkspaceScreen] state:connectors", {
      tenantId: tenant?.id,
      count: connectors.length,
      connectors
    });
  }, [connectors, tenant?.id]);

  const runQuery = async ({ question, sourceType, sourceId }) => {
    setPending(true);
    console.log("[AISX][WorkspaceScreen] runQuery:start", {
      tenantId: tenant?.id,
      question,
      sourceType,
      sourceId
    });
    try {
      const payload = {
        question,
        tenantId: tenant?.id || null,
        sourceType,
        sourceId
      };
      const { data } = await queryApi.ask(payload);
      const normalized = data?.data || data;
      console.log("[AISX][WorkspaceScreen] runQuery:success", {
        tenantId: tenant?.id,
        payload,
        response: data,
        normalized
      });
      setResult(normalized);
      setHistory((prev) => [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          question,
          sourceType,
          sourceId,
          answer: normalized
        },
        ...prev
      ].slice(0, 8));
    } catch (error) {
      console.error("[AISX][WorkspaceScreen] runQuery:error", {
        tenantId: tenant?.id,
        error,
        response: error?.response?.data
      });
      toast.error(parseApiError(error, "Query failed"));
    } finally {
      console.log("[AISX][WorkspaceScreen] runQuery:done", { tenantId: tenant?.id });
      setPending(false);
    }
  };

  return (
    <section className="grid-three">
      <div className="panel" style={{ gridColumn: "1 / -1" }}>
        <h3>How To Ask Questions</h3>
        <p>
          Use this page to ask questions for both uploaded sheets and connected databases.
          Type your question in <strong>Ask Your Data</strong>, then click <strong>Run</strong>.
        </p>
      </div>
      <ChatWindow onSend={runQuery} pending={pending} files={files} connectors={connectors} />
      <ResultPanel result={result} history={history} />
      <ExplainabilityDrawer result={result} />
    </section>
  );
}
